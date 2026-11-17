/**
 * @file src/platform/windows/touch_ux.cpp
 * @brief Windows implementation of the touch session UX helper (see src/touch_ux.h).
 *
 * Keyboard assist runs in a separate process (tools\sunshine_touch_ux.exe) so a fault in the shell
 * or UI Automation can never take the host down. The host launches it, writes one byte to its
 * standard input for every injected touch (throttled), and closes the pipe when the last touch
 * session ends. The helper stops by itself when the pipe closes.
 *
 * Taskbar: the primary taskbar is switched to auto-hide with SHAppBarMessage for the duration of
 * the touch session. A journal file records the original state so a crashed host restores it at
 * the next start.
 */
// standard includes
#include <array>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <thread>

// platform includes
#include <winsock2.h>
#include <windows.h>

#include <shellapi.h>

// local includes
#include "src/config.h"
#include "src/logging.h"
#include "src/platform/common.h"
#include "src/touch_ux.h"
#include "src/touch_ux_policy.h"

using namespace std::literals;

namespace touch_ux {
  namespace {
    std::mutex lifecycle_mutex;
    int ref_count = 0;
    std::thread worker;

    std::atomic<bool> touch_pending {false};

    std::mutex event_mutex;
    std::condition_variable event_cv;
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

    struct helper_t {
      HANDLE process = nullptr;
      HANDLE pipe = nullptr;  ///< Write end of the helper's standard input.
    };

    /// Launch the keyboard assist helper next to the host executable with a pipe as its standard input.
    bool helper_start(helper_t &helper) {
      std::array<wchar_t, 32768> executable {};
      const DWORD length = GetModuleFileNameW(nullptr, executable.data(), static_cast<DWORD>(executable.size()));
      if (length == 0 || length >= executable.size()) {
        return false;
      }
      const auto directory = std::filesystem::path(executable.data()).parent_path();
      const auto exe = directory / L"tools" / L"sunshine_touch_ux.exe";
      std::error_code ec;
      if (!std::filesystem::exists(exe, ec)) {
        BOOST_LOG(warning) << "touch_ux: keyboard assist helper not found: "sv << exe.string();
        return false;
      }

      SECURITY_ATTRIBUTES inheritable {sizeof(inheritable), nullptr, TRUE};
      HANDLE read_end = nullptr;
      HANDLE write_end = nullptr;
      if (!CreatePipe(&read_end, &write_end, &inheritable, 0)) {
        return false;
      }
      SetHandleInformation(write_end, HANDLE_FLAG_INHERIT, 0);  // the helper must not inherit our write end

      HANDLE null_out = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_WRITE, &inheritable, OPEN_EXISTING, 0, nullptr);

      std::wstring command = L"\"" + exe.wstring() + L"\" \"" + platf::appdata().wstring() + L"\"";
      STARTUPINFOW startup {};
      startup.cb = sizeof(startup);
      startup.dwFlags = STARTF_USESTDHANDLES;
      startup.hStdInput = read_end;
      startup.hStdOutput = null_out;
      startup.hStdError = null_out;
      PROCESS_INFORMATION child {};
      const BOOL started = CreateProcessW(exe.c_str(), command.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, directory.c_str(), &startup, &child);
      const DWORD launch_error = started ? ERROR_SUCCESS : GetLastError();
      CloseHandle(read_end);
      if (null_out != INVALID_HANDLE_VALUE) {
        CloseHandle(null_out);
      }
      if (!started) {
        BOOST_LOG(warning) << "touch_ux: could not launch the keyboard assist helper, error "sv << launch_error;
        CloseHandle(write_end);
        return false;
      }
      CloseHandle(child.hThread);
      helper.process = child.hProcess;
      helper.pipe = write_end;
      BOOST_LOG(info) << "touch_ux: touch keyboard assist helper started"sv;
      return true;
    }

    void helper_stop(helper_t &helper) {
      if (helper.pipe) {
        CloseHandle(helper.pipe);  // end of input: the helper exits by itself
        helper.pipe = nullptr;
      }
      if (helper.process) {
        if (WaitForSingleObject(helper.process, 2000) == WAIT_TIMEOUT) {
          TerminateProcess(helper.process, 0);
          WaitForSingleObject(helper.process, 1000);
        }
        CloseHandle(helper.process);
        helper.process = nullptr;
      }
    }

    void worker_main(bool keyboard_assist, bool taskbar_autohide) {
      bool taskbar_changed = false;
      if (taskbar_autohide) {
        taskbar_changed = taskbar_hide();
      }

      helper_t helper;
      bool helper_running = keyboard_assist && helper_start(helper);
      auto last_write = std::chrono::steady_clock::now() - 1s;

      std::unique_lock lock {event_mutex};
      while (!stop_requested) {
        event_cv.wait_for(lock, 100ms, [] {
          return stop_requested;
        });
        if (stop_requested) {
          break;
        }
        if (!helper_running) {
          continue;
        }
        if (WaitForSingleObject(helper.process, 0) != WAIT_TIMEOUT) {
          DWORD code = 0;
          GetExitCodeProcess(helper.process, &code);
          BOOST_LOG(warning) << "touch_ux: keyboard assist helper exited with code "sv << code << "; keyboard assist is off for this session"sv;
          helper_running = false;
          continue;
        }
        if (touch_pending.exchange(false) && std::chrono::steady_clock::now() - last_write >= 100ms) {
          const char byte = 'T';
          DWORD written = 0;
          WriteFile(helper.pipe, &byte, 1, &written, nullptr);
          last_write = std::chrono::steady_clock::now();
        }
      }
      lock.unlock();

      helper_stop(helper);
      if (taskbar_changed) {
        taskbar_restore();
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
    }
    touch_pending = false;
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
    touch_pending.store(true, std::memory_order_relaxed);
  }

  void recover() {
    std::error_code ec;
    if (std::filesystem::exists(journal_path(), ec)) {
      BOOST_LOG(warning) << "touch_ux: restoring the taskbar left auto-hidden by a previous run"sv;
      taskbar_restore();
    }
  }
}  // namespace touch_ux
