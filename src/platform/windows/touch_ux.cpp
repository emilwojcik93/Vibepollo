/**
 * @file src/platform/windows/touch_ux.cpp
 * @brief Windows implementation of the touch session UX helper (see src/touch_ux.h).
 *
 * Keyboard assist: a UI Automation focus-changed handler reports every focus change. When a text
 * field gets focus within a short time after injected touch, the shell touch keyboard is shown
 * through ITipInvocation::Toggle (only when IFrameworkInputPane reports it hidden). When focus
 * leaves text fields the keyboard is hidden again, but only if this helper showed it.
 *
 * Taskbar: the primary taskbar is switched to auto-hide with SHAppBarMessage for the duration of
 * the touch session. A journal file records the original state so a crashed host restores it at
 * the next start.
 */
// standard includes
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <optional>
#include <thread>

// platform includes
#include <winsock2.h>
#include <windows.h>

#include <objbase.h>
#include <shellapi.h>
#include <unknwn.h>
#include <uiautomationclient.h>
#include <wrl/client.h>

// local includes
#include "src/config.h"
#include "src/logging.h"
#include "src/platform/common.h"
#include "src/touch_ux.h"
#include "src/touch_ux_policy.h"

using namespace std::literals;

namespace touch_ux {
  namespace {
    using Microsoft::WRL::ComPtr;

    // GUIDs and minimal interface declarations kept local so the build does not depend on which
    // optional shell headers the toolchain ships.
    const GUID clsid_cuiautomation = {0xff48dba4, 0x60ef, 0x4201, {0xaa, 0x87, 0x54, 0x10, 0x3e, 0xef, 0x59, 0x4e}};
    const GUID iid_iuiautomation = {0x30cbe57d, 0xd9d0, 0x452a, {0xab, 0x13, 0x7a, 0xc5, 0xac, 0x48, 0x25, 0xee}};
    const GUID iid_focus_handler = {0xc270f6b5, 0x5c69, 0x4290, {0x97, 0x45, 0x7a, 0x7f, 0x97, 0x16, 0x94, 0x68}};
    const GUID iid_value_pattern = {0xa94cd8b1, 0x0844, 0x4cd6, {0x9d, 0x2d, 0x64, 0x05, 0x37, 0xab, 0x39, 0xe9}};

    const GUID clsid_tip_invocation = {0x4ce576fa, 0x83dc, 0x4f88, {0x95, 0x1c, 0x9d, 0x07, 0x82, 0xb4, 0xe3, 0x76}};
    const GUID iid_tip_invocation = {0x37c994e7, 0x432b, 0x4834, {0xa2, 0xf7, 0xdc, 0xe1, 0xf1, 0x3b, 0x83, 0x4b}};
    const GUID clsid_input_pane = {0xd5120aa3, 0x46ba, 0x44c5, {0x82, 0x2d, 0xca, 0x80, 0x92, 0xc1, 0xfc, 0x72}};
    const GUID iid_input_pane = {0x5752238b, 0x24f0, 0x495a, {0x82, 0xf1, 0x2f, 0xd5, 0x93, 0x05, 0x67, 0x96}};

    struct ITipInvocationLocal: IUnknown {
      virtual HRESULT STDMETHODCALLTYPE Toggle(HWND wnd) = 0;
    };

    struct IFrameworkInputPaneLocal: IUnknown {
      virtual HRESULT STDMETHODCALLTYPE Advise(IUnknown *window, IUnknown *handler, DWORD *cookie) = 0;
      virtual HRESULT STDMETHODCALLTYPE AdviseWithHWND(HWND wnd, IUnknown *handler, DWORD *cookie) = 0;
      virtual HRESULT STDMETHODCALLTYPE Unadvise(DWORD cookie) = 0;
      virtual HRESULT STDMETHODCALLTYPE Location(RECT *rect) = 0;
    };

    std::mutex lifecycle_mutex;
    int ref_count = 0;
    std::thread worker;

    std::atomic<long long> last_touch_ticks {0};  // steady_clock ticks, 0 = never

    struct focus_event_t {
      bool editable = false;
      bool pending = false;
    };

    std::mutex event_mutex;
    std::condition_variable event_cv;
    focus_event_t pending_event;
    bool stop_requested = false;

    std::filesystem::path journal_path() {
      return platf::appdata() / "touch_taskbar_autohide.journal";
    }

    UINT taskbar_state(HWND tray) {
      APPBARDATA data {};
      data.cbSize = sizeof(data);
      data.hWnd = tray;
      return static_cast<UINT>(SHAppBarMessage(ABM_GETSTATE, &data));
    }

    void set_taskbar_state(HWND tray, UINT state) {
      APPBARDATA data {};
      data.cbSize = sizeof(data);
      data.hWnd = tray;
      data.lParam = static_cast<LPARAM>(state);
      SHAppBarMessage(ABM_SETSTATE, &data);
    }

    /// Switch the taskbar to auto-hide. Returns true when this call changed it (and wrote the journal).
    bool taskbar_hide() {
      HWND tray = FindWindowW(L"Shell_TrayWnd", nullptr);
      if (!tray) {
        BOOST_LOG(warning) << "touch_ux: no taskbar window, skipping auto-hide"sv;
        return false;
      }
      const UINT current = taskbar_state(tray);
      if (current & ABS_AUTOHIDE) {
        return false;  // already auto-hide: nothing to restore later
      }
      std::ofstream(journal_path(), std::ios::trunc) << current;
      set_taskbar_state(tray, touch_ux::taskbar_state_for_touch(current, ABS_AUTOHIDE));
      BOOST_LOG(info) << "touch_ux: taskbar set to auto-hide for the touch session"sv;
      return true;
    }

    void taskbar_restore() {
      HWND tray = FindWindowW(L"Shell_TrayWnd", nullptr);
      if (tray) {
        set_taskbar_state(tray, touch_ux::taskbar_state_restored(taskbar_state(tray), ABS_AUTOHIDE, false));
        BOOST_LOG(info) << "touch_ux: taskbar auto-hide restored"sv;
      }
      std::error_code ec;
      std::filesystem::remove(journal_path(), ec);
    }

    bool osk_visible() {
      ComPtr<IFrameworkInputPaneLocal> pane;
      if (FAILED(CoCreateInstance(clsid_input_pane, nullptr, CLSCTX_INPROC_SERVER, iid_input_pane, reinterpret_cast<void **>(pane.GetAddressOf())))) {
        return false;
      }
      RECT rect {};
      if (FAILED(pane->Location(&rect))) {
        return false;
      }
      return rect.right - rect.left > 0 && rect.bottom - rect.top > 0;
    }

    void osk_toggle() {
      ComPtr<ITipInvocationLocal> tip;
      if (FAILED(CoCreateInstance(clsid_tip_invocation, nullptr, CLSCTX_LOCAL_SERVER, iid_tip_invocation, reinterpret_cast<void **>(tip.GetAddressOf())))) {
        BOOST_LOG(debug) << "touch_ux: touch keyboard COM object unavailable"sv;
        return;
      }
      tip->Toggle(GetDesktopWindow());
    }

    bool element_is_editable(IUIAutomationElement *element) {
      CONTROLTYPEID type = 0;
      if (FAILED(element->get_CurrentControlType(&type))) {
        return false;
      }
      if (type != UIA_EditControlTypeId && type != UIA_DocumentControlTypeId) {
        return false;
      }
      ComPtr<IUIAutomationValuePattern> value;
      const HRESULT hr = element->GetCurrentPatternAs(UIA_ValuePatternId, iid_value_pattern, reinterpret_cast<void **>(value.GetAddressOf()));
      if (SUCCEEDED(hr) && value) {
        BOOL read_only = FALSE;
        if (SUCCEEDED(value->get_CurrentIsReadOnly(&read_only))) {
          return !read_only;
        }
      }
      // Edit controls without a value pattern are still text fields; documents need the pattern.
      return type == UIA_EditControlTypeId;
    }

    class focus_handler_t final: public IUIAutomationFocusChangedEventHandler {
    public:
      HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **object) override {
        if (!object) {
          return E_POINTER;
        }
        if (IsEqualIID(riid, IID_IUnknown) || IsEqualIID(riid, iid_focus_handler)) {
          *object = static_cast<IUIAutomationFocusChangedEventHandler *>(this);
          AddRef();
          return S_OK;
        }
        *object = nullptr;
        return E_NOINTERFACE;
      }

      ULONG STDMETHODCALLTYPE AddRef() override {
        return ++refs_;
      }

      ULONG STDMETHODCALLTYPE Release() override {
        const ULONG remaining = --refs_;
        if (remaining == 0) {
          delete this;
        }
        return remaining;
      }

      HRESULT STDMETHODCALLTYPE HandleFocusChangedEvent(IUIAutomationElement *sender) override {
        if (!sender) {
          return S_OK;
        }
        const bool editable = element_is_editable(sender);
        {
          std::lock_guard lg {event_mutex};
          pending_event = {editable, true};
        }
        event_cv.notify_one();
        return S_OK;
      }

    private:
      std::atomic<ULONG> refs_ {1};
    };

    void worker_main(bool keyboard_assist, bool taskbar_autohide) {
      const HRESULT co = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
      const bool co_ok = SUCCEEDED(co);

      bool taskbar_changed = false;
      if (taskbar_autohide) {
        taskbar_changed = taskbar_hide();
      }

      ComPtr<IUIAutomation> automation;
      ComPtr<focus_handler_t> handler;
      if (keyboard_assist) {
        if (SUCCEEDED(CoCreateInstance(clsid_cuiautomation, nullptr, CLSCTX_INPROC_SERVER, iid_iuiautomation, reinterpret_cast<void **>(automation.GetAddressOf())))) {
          handler.Attach(new focus_handler_t());
          if (FAILED(automation->AddFocusChangedEventHandler(nullptr, handler.Get()))) {
            BOOST_LOG(warning) << "touch_ux: could not register the focus handler, keyboard assist disabled"sv;
            handler.Reset();
          } else {
            BOOST_LOG(info) << "touch_ux: touch keyboard assist active"sv;
          }
        } else {
          BOOST_LOG(warning) << "touch_ux: UI Automation unavailable, keyboard assist disabled"sv;
        }
      }

      bool we_showed = false;
      std::optional<std::chrono::steady_clock::time_point> hide_at;

      std::unique_lock lock {event_mutex};
      while (!stop_requested) {
        if (hide_at) {
          event_cv.wait_until(lock, *hide_at, [] {
            return stop_requested || pending_event.pending;
          });
        } else {
          event_cv.wait(lock, [] {
            return stop_requested || pending_event.pending;
          });
        }
        if (stop_requested) {
          break;
        }

        const auto now = std::chrono::steady_clock::now();
        if (pending_event.pending) {
          const focus_event_t event = pending_event;
          pending_event.pending = false;
          lock.unlock();

          touch_ux::focus_state_t state;
          state.editable = event.editable;
          state.osk_visible = osk_visible();
          state.we_showed = we_showed;
          const long long ticks = last_touch_ticks.load();
          state.touch_recent = ticks != 0 && touch_ux::touch_is_recent(std::chrono::steady_clock::time_point {std::chrono::steady_clock::duration {ticks}}, now);

          switch (touch_ux::decide(state)) {
            case touch_ux::osk_action_e::show:
              osk_toggle();
              we_showed = true;
              hide_at.reset();
              break;
            case touch_ux::osk_action_e::schedule_hide:
              hide_at = now + touch_ux::hide_delay;
              break;
            case touch_ux::osk_action_e::forget:
              we_showed = false;
              hide_at.reset();
              break;
            case touch_ux::osk_action_e::none:
              if (state.editable) {
                hide_at.reset();
              }
              break;
          }
          lock.lock();
        } else if (hide_at && now >= *hide_at) {
          hide_at.reset();
          lock.unlock();
          if (we_showed && osk_visible()) {
            osk_toggle();
          }
          we_showed = false;
          lock.lock();
        }
      }
      lock.unlock();

      if (automation && handler) {
        automation->RemoveFocusChangedEventHandler(handler.Get());
      }
      handler.Reset();
      automation.Reset();

      if (taskbar_changed) {
        taskbar_restore();
      }
      if (co_ok) {
        CoUninitialize();
      }
    }
  }  // namespace

  void acquire() {
    std::lock_guard lg {lifecycle_mutex};
    if (ref_count++ > 0) {
      return;
    }
    const bool keyboard_assist = config::video.touch_keyboard_assist;
    const bool taskbar_autohide = config::video.touch_taskbar_autohide;
    if (!keyboard_assist && !taskbar_autohide) {
      return;
    }
    {
      std::lock_guard ev {event_mutex};
      stop_requested = false;
      pending_event = {};
    }
    worker = std::thread(worker_main, keyboard_assist, taskbar_autohide);
  }

  void release() {
    std::lock_guard lg {lifecycle_mutex};
    if (ref_count == 0 || --ref_count > 0) {
      return;
    }
    if (!worker.joinable()) {
      return;
    }
    {
      std::lock_guard ev {event_mutex};
      stop_requested = true;
    }
    event_cv.notify_all();
    worker.join();
  }

  void note_touch() {
    last_touch_ticks.store(std::chrono::steady_clock::now().time_since_epoch().count(), std::memory_order_relaxed);
  }

  void recover() {
    std::error_code ec;
    if (std::filesystem::exists(journal_path(), ec)) {
      BOOST_LOG(warning) << "touch_ux: restoring the taskbar left auto-hidden by a previous run"sv;
      taskbar_restore();
    }
  }
}  // namespace touch_ux
