/**
 * @file tools/touch_ux_helper.cpp
 * @brief Touch keyboard assist helper, run by the host while a touch-primary client streams.
 *
 * Windows shows its touch keyboard only for focus that came from a physical touch device, so
 * touch injected for a streaming client never brings it up. The host starts this helper in the
 * interactive session and writes one byte to its standard input for every injected touch. While
 * touch is recent (or while the keyboard is one this helper showed), the helper polls the focused
 * UI Automation element: a text field that gets focus shows the touch keyboard, and focus leaving
 * text fields hides it again. End of input (host exit or session end) stops the helper.
 *
 * It runs in its own process so a fault in the shell or UI Automation components can never take
 * the streaming host down. Usage: sunshine_touch_ux [log-directory]
 */
#include <winsock2.h>
#include <windows.h>

#include <objbase.h>
#include <uiautomationclient.h>
#include <wrl/client.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <string>

using Microsoft::WRL::ComPtr;
using namespace std::chrono_literals;

namespace {
  const GUID clsid_cuiautomation = {0xff48dba4, 0x60ef, 0x4201, {0xaa, 0x87, 0x54, 0x10, 0x3e, 0xef, 0x59, 0x4e}};
  const GUID iid_iuiautomation = {0x30cbe57d, 0xd9d0, 0x452a, {0xab, 0x13, 0x7a, 0xc5, 0xac, 0x48, 0x25, 0xee}};
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

  constexpr auto touch_recent_window = 2500ms;
  constexpr auto hide_delay = 350ms;
  constexpr auto poll_interval = 200ms;

  std::atomic<long long> last_touch_ticks {0};
  std::atomic<bool> stop_requested {false};
  FILE *log_file = nullptr;

  void log_line(const char *text, long value = 0) {
    if (!log_file) {
      return;
    }
    std::fprintf(log_file, "%s %ld\n", text, value);
    std::fflush(log_file);
  }

  /// Create a COM object through a void* out parameter and adopt it. Casting a typed interface
  /// pointer's address to void** instead breaks strict aliasing, and optimized builds then call
  /// through a stale null pointer.
  template<class T>
  ComPtr<T> create_instance(const GUID &clsid, DWORD context, const GUID &iid) {
    void *raw = nullptr;
    ComPtr<T> result;
    if (SUCCEEDED(CoCreateInstance(clsid, nullptr, context, iid, &raw)) && raw) {
      result.Attach(static_cast<T *>(raw));
    }
    return result;
  }

  bool osk_visible() {
    ComPtr<IFrameworkInputPaneLocal> pane = create_instance<IFrameworkInputPaneLocal>(clsid_input_pane, CLSCTX_INPROC_SERVER, iid_input_pane);
    if (!pane) {
      return false;
    }
    RECT rect {};
    if (FAILED(pane->Location(&rect))) {
      return false;
    }
    return rect.right - rect.left > 0 && rect.bottom - rect.top > 0;
  }

  void osk_toggle() {
    ComPtr<ITipInvocationLocal> tip = create_instance<ITipInvocationLocal>(clsid_tip_invocation, CLSCTX_LOCAL_SERVER, iid_tip_invocation);
    if (!tip) {
      log_line("touch keyboard object unavailable");
      return;
    }
    tip->Toggle(GetDesktopWindow());
  }

  bool focused_element_is_editable(IUIAutomation *automation) {
    ComPtr<IUIAutomationElement> element;
    if (FAILED(automation->GetFocusedElement(element.GetAddressOf())) || !element) {
      return false;
    }
    CONTROLTYPEID type = 0;
    if (FAILED(element->get_CurrentControlType(&type))) {
      return false;
    }
    if (type != UIA_EditControlTypeId && type != UIA_DocumentControlTypeId) {
      return false;
    }
    void *pattern = nullptr;
    ComPtr<IUIAutomationValuePattern> value;
    if (SUCCEEDED(element->GetCurrentPatternAs(UIA_ValuePatternId, iid_value_pattern, &pattern)) && pattern) {
      value.Attach(static_cast<IUIAutomationValuePattern *>(pattern));
    }
    if (value) {
      BOOL read_only = FALSE;
      if (SUCCEEDED(value->get_CurrentIsReadOnly(&read_only))) {
        return !read_only;
      }
    }
    // Edit controls without a value pattern are still text fields; documents need the pattern.
    return type == UIA_EditControlTypeId;
  }

  DWORD WINAPI stdin_reader(LPVOID) {
    HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
    char buffer[64];
    DWORD got = 0;
    while (in != INVALID_HANDLE_VALUE && ReadFile(in, buffer, sizeof(buffer), &got, nullptr) && got > 0) {
      last_touch_ticks.store(std::chrono::steady_clock::now().time_since_epoch().count());
    }
    stop_requested = true;  // host closed the pipe or exited
    return 0;
  }

  bool touch_is_recent(std::chrono::steady_clock::time_point now) {
    const long long ticks = last_touch_ticks.load();
    if (ticks == 0) {
      return false;
    }
    const std::chrono::steady_clock::time_point last {std::chrono::steady_clock::duration {ticks}};
    return now >= last && now - last <= touch_recent_window;
  }
}  // namespace

int main(int argc, char **argv) {
  if (argc > 1) {
    const std::string path = std::string(argv[1]) + "\\touch_ux_helper.log";
    log_file = std::fopen(path.c_str(), "w");
  }
  log_line("start");

  const HRESULT co = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
  log_line("coinit", co);

  ComPtr<IUIAutomation> automation = create_instance<IUIAutomation>(clsid_cuiautomation, CLSCTX_INPROC_SERVER, iid_iuiautomation);
  log_line("uia create", automation ? 0 : 1);
  if (!automation) {
    return 2;
  }

  HANDLE reader = CreateThread(nullptr, 0, stdin_reader, nullptr, 0, nullptr);
  if (!reader) {
    return 3;
  }

  bool we_showed = false;
  bool last_editable = false;
  bool first_poll = true;
  bool first_toggle = true;
  std::chrono::steady_clock::time_point hide_at {};
  bool hide_pending = false;

  while (!stop_requested) {
    Sleep(static_cast<DWORD>(poll_interval.count()));
    const auto now = std::chrono::steady_clock::now();
    const bool recent = touch_is_recent(now);
    if (!recent && !we_showed) {
      last_editable = false;
      hide_pending = false;
      continue;
    }
    if (first_poll) {
      log_line("first poll");
      first_poll = false;
    }
    const bool editable = focused_element_is_editable(automation.Get());
    const bool visible = osk_visible();
    if (editable) {
      hide_pending = false;
      if (!visible && recent && !last_editable) {
        if (first_toggle) {
          log_line("first toggle");
          first_toggle = false;
        }
        osk_toggle();
        we_showed = true;
      }
    } else if (we_showed) {
      if (!visible) {
        we_showed = false;  // the user closed it
        hide_pending = false;
      } else if (!hide_pending) {
        hide_pending = true;
        hide_at = now + hide_delay;
      } else if (now >= hide_at) {
        osk_toggle();
        we_showed = false;
        hide_pending = false;
      }
    }
    last_editable = editable;
  }

  if (we_showed && osk_visible()) {
    osk_toggle();
  }
  log_line("exit");
  automation.Reset();
  CoUninitialize();
  CloseHandle(reader);
  if (log_file) {
    std::fclose(log_file);
  }
  return 0;
}
