/**
 * @file src/platform/windows/gaming_posture.cpp
 * @brief Windows backend for the gaming posture, the Xbox full screen experience (see src/tablet_posture.h).
 *
 * Windows calls the Xbox full screen experience ("Xbox mode") its gaming posture. The shell's
 * GamingPostureController enters and leaves it, and gamemode.dll exposes the state through
 * IsGamingFullScreenExperienceSupported/Active and a switch, SetGamingFullScreenExperience, with
 * CanSetGamingFullScreenExperience telling whether the switch is available. It is once the user has a
 * home app (Settings > Gaming > Xbox mode, normally the Xbox app); without one nothing can enter.
 *
 * Windows also offers every user the Win+F11 shortcut, handled by the same controller. This backend
 * never toggles blindly: it reads the state, uses the switch when Windows allows it, otherwise
 * presses the shortcut only when the state differs, and then confirms the new state. When the
 * experience is not supported, has no home app, or does not change, the caller keeps the host in
 * tablet mode.
 *
 * Windows refuses the switch (E_ACCESSDENIED) for a process that does not run as the logged-on user,
 * also while such a process impersonates that user. A host service therefore starts this executable
 * once more as the user, with gaming_switch_arg: that instance only calls the switch and exits.
 *
 * Entering and leaving ask for confirmation. Windows remembers the answers in the user's hive under
 * GamingConfiguration\SystemDialogResults; the machine hive is not consulted. The answers are
 * supplied there only while the host switches and removed afterwards; a journal file lets a
 * restarted host remove them after a crash. A choice the user made is never touched.
 */
// standard includes
#include <algorithm>
#include <array>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>
#include <vector>

// platform includes
#include <winsock2.h>
#include <windows.h>

// local includes
#include "misc.h"
#include "src/logging.h"
#include "src/platform/common.h"
#include "src/tablet_posture.h"

using namespace std::literals;

namespace tablet_posture::backend {
  bool apply_gaming(bool enter);

  void recover_gaming();
}  // namespace tablet_posture::backend

namespace tablet_posture::backend {
  namespace {
    constexpr auto dialog_results_key = L"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\GamingConfiguration\\SystemDialogResults";

    struct dialog_answer_t {
      const wchar_t *name;
      DWORD answer;
    };

    /// The confirmations Windows shows around a switch, each with the answer that switches at once.
    /// Which of them appear depends on the device form Windows believes it runs on.
    constexpr std::array<dialog_answer_t, 5> dialog_answers {{
      {L"EnterGamingPostureConfirmation_NoReboot", 1},
      // Handheld device form: "Enter at startup", "Start now", "Stay on desktop". 2 is "Start now" (build 26300).
      {L"EnterGamingPostureConfirmation_Config", 2},
      {L"PostEntryGamingPostureConfirmation", 1},
      {L"ExitGamingPostureConfirmation_Minimal", 1},
      {L"ExitGamingPostureConfirmation", 1},
    }};

    constexpr auto switch_timeout = 8s;
    constexpr auto switch_poll = 100ms;

    using state_fn_t = BOOL(WINAPI *)();
    using switch_fn_t = HRESULT(WINAPI *)(BOOL);

    struct gaming_api_t {
      state_fn_t supported = nullptr;
      state_fn_t active = nullptr;
      state_fn_t can_set = nullptr;
      switch_fn_t set = nullptr;
    };

    /// The gaming experience API set is missing on older Windows, so it is bound at run time.
    const gaming_api_t &gaming_api() {
      static const gaming_api_t api = [] {
        gaming_api_t loaded;
        if (const auto module = LoadLibraryW(L"api-ms-win-gaming-experience-l1-1-0.dll")) {
          loaded.supported = reinterpret_cast<state_fn_t>(reinterpret_cast<void *>(GetProcAddress(module, "IsGamingFullScreenExperienceSupported")));
          loaded.active = reinterpret_cast<state_fn_t>(reinterpret_cast<void *>(GetProcAddress(module, "IsGamingFullScreenExperienceActive")));
          loaded.can_set = reinterpret_cast<state_fn_t>(reinterpret_cast<void *>(GetProcAddress(module, "CanSetGamingFullScreenExperience")));
          loaded.set = reinterpret_cast<switch_fn_t>(reinterpret_cast<void *>(GetProcAddress(module, "SetGamingFullScreenExperience")));
        }
        return loaded;
      }();
      return api;
    }

    std::filesystem::path journal_path() {
      return platf::appdata() / "gaming_posture.journal";
    }

    std::string narrow(const wchar_t *text) {
      std::string out;
      for (; *text; ++text) {
        out.push_back(static_cast<char>(*text));
      }
      return out;
    }

    /// The logged-on user's hive, or this process' own when the host already runs as that user. Close with close_user_hive().
    HKEY open_user_hive() {
      HKEY hive = nullptr;
      if (const HANDLE token = platf::retrieve_users_token(false)) {
        (void) platf::impersonate_current_user(token, [&]() {
          if (RegOpenCurrentUser(KEY_READ | KEY_WRITE, &hive) != ERROR_SUCCESS) {
            hive = nullptr;
          }
        });
        CloseHandle(token);
      }
      return hive ? hive : HKEY_CURRENT_USER;
    }

    void close_user_hive(HKEY hive) {
      if (hive != HKEY_CURRENT_USER) {
        RegCloseKey(hive);
      }
    }

    /// Remove the confirmation answers this host supplied, as listed in the journal.
    void withdraw_answers() {
      std::error_code ec;
      if (!std::filesystem::exists(journal_path(), ec)) {
        return;
      }
      std::vector<std::string> supplied;
      {
        std::ifstream in(journal_path());
        for (std::string line; std::getline(in, line);) {
          if (!line.empty()) {
            supplied.push_back(line);
          }
        }
      }
      HKEY key = nullptr;
      bool clean = true;
      const HKEY hive = open_user_hive();
      if (RegOpenKeyExW(hive, dialog_results_key, 0, KEY_SET_VALUE, &key) == ERROR_SUCCESS) {
        for (const auto &dialog : dialog_answers) {
          if (std::find(supplied.begin(), supplied.end(), narrow(dialog.name)) == supplied.end()) {
            continue;
          }
          const auto status = RegDeleteValueW(key, dialog.name);
          clean = clean && (status == ERROR_SUCCESS || status == ERROR_FILE_NOT_FOUND);
        }
        RegCloseKey(key);
      }
      close_user_hive(hive);
      if (clean) {
        std::filesystem::remove(journal_path(), ec);
      } else {
        BOOST_LOG(warning) << "Gaming posture: could not remove the confirmation answers; they stay journaled for the next start"sv;
      }
    }

    /// Answer the switch confirmations in the user's hive where no answer exists yet, journaling each one first.
    void supply_answers() {
      HKEY key = nullptr;
      const HKEY hive = open_user_hive();
      const auto status = RegCreateKeyExW(hive, dialog_results_key, 0, nullptr, 0, KEY_QUERY_VALUE | KEY_SET_VALUE, nullptr, &key, nullptr);
      close_user_hive(hive);
      if (status != ERROR_SUCCESS) {
        BOOST_LOG(warning) << "Gaming posture: could not open the confirmation answers; Windows may ask before switching"sv;
        return;
      }
      for (const auto &dialog : dialog_answers) {
        if (RegQueryValueExW(key, dialog.name, nullptr, nullptr, nullptr, nullptr) == ERROR_SUCCESS) {
          continue;  // Already answered, by the user or by an earlier switch of ours.
        }
        std::ofstream(journal_path(), std::ios::app) << narrow(dialog.name) << '\n';
        RegSetValueExW(key, dialog.name, 0, REG_DWORD, reinterpret_cast<const BYTE *>(&dialog.answer), sizeof(dialog.answer));
      }
      RegCloseKey(key);
    }

    bool wait_for_active(bool wanted) {
      const auto &api = gaming_api();
      const auto deadline = std::chrono::steady_clock::now() + switch_timeout;
      while (std::chrono::steady_clock::now() < deadline) {
        if ((api.active() != FALSE) == wanted) {
          return true;
        }
        std::this_thread::sleep_for(switch_poll);
      }
      return (api.active() != FALSE) == wanted;
    }

    /// Ask Windows for the switch from this process. The call does not return while Windows shows a
    /// confirmation, so it runs on its own thread; callers bound it with wait_for_active().
    void request_switch(bool enter) {
      std::thread([set = gaming_api().set, enter] {
        set(enter ? TRUE : FALSE);
      }).detach();
    }

    /// Start this executable as the logged-on user to make the switch there.
    bool request_switch_as_user(bool enter) {
      wchar_t executable[MAX_PATH] = {};
      const DWORD length = GetModuleFileNameW(nullptr, executable, ARRAYSIZE(executable));
      if (length == 0 || length >= ARRAYSIZE(executable)) {
        return false;
      }
      std::string cmd = "\"" + platf::to_utf8(executable) + "\" ";
      cmd += tablet_posture::gaming_switch_arg;
      cmd += enter ? " enter" : " leave";

      platf::bp::environment env = platf::bp::this_process::env();
      auto working_dir = boost::filesystem::path();
      std::error_code ec;
      auto child = platf::run_command(false, false, cmd, working_dir, env, nullptr, ec, nullptr);
      if (ec) {
        BOOST_LOG(warning) << "Gaming posture: could not start the switch as the logged-on user: "sv << ec.message();
        return false;
      }
      child.detach();
      return true;
    }

    /// Press Win+F11 on the input desktop, the shortcut Windows documents for this switch.
    bool press_shortcut() {
      platf::syncThreadDesktop();
      std::array<INPUT, 4> keys {};
      const auto key = [](INPUT &slot, WORD code, bool release) {
        slot.type = INPUT_KEYBOARD;
        slot.ki.wVk = code;
        slot.ki.dwFlags = release ? KEYEVENTF_KEYUP : 0;
      };
      key(keys[0], VK_LWIN, false);
      key(keys[1], VK_F11, false);
      key(keys[2], VK_F11, true);
      key(keys[3], VK_LWIN, true);
      return SendInput(static_cast<UINT>(keys.size()), keys.data(), sizeof(INPUT)) == keys.size();
    }
  }  // namespace

  bool apply_gaming(bool enter) {
    const auto &api = gaming_api();
    if (!api.supported || !api.active || api.supported() == FALSE) {
      if (enter) {
        BOOST_LOG(warning) << "Gaming posture: this Windows does not offer the Xbox full screen experience"sv;
      }
      return !enter;
    }

    if ((api.active() != FALSE) == enter) {
      if (!enter) {
        withdraw_answers();
      }
      return true;
    }

    supply_answers();

    bool switched = false;
    if (api.set && api.can_set && api.can_set() != FALSE) {
      // This host runs as the logged-on user and that user has a home app.
      request_switch(enter);
      switched = wait_for_active(enter);
    } else if (api.set && request_switch_as_user(enter)) {
      switched = wait_for_active(enter);
    }
    if (!switched) {
      if (!press_shortcut()) {
        BOOST_LOG(warning) << "Gaming posture: could not send the Win+F11 shortcut"sv;
      }
      switched = wait_for_active(enter);
    }

    if (!switched && enter) {
      BOOST_LOG(warning) << "Gaming posture: Windows did not enter the Xbox full screen experience. "
                            "Check that the Xbox app is installed and chosen as the home app under "
                            "Settings > Gaming > Xbox mode, and that no confirmation is waiting on the host screen."sv;
    }
    if (!enter || !switched) {
      // Nothing more to confirm once the host is back on the desktop, or when it never left it.
      if ((api.active() != FALSE) == false) {
        withdraw_answers();
      }
    }
    return switched;
  }

  void recover_gaming() {
    const auto &api = gaming_api();
    if (api.active && api.active() != FALSE) {
      return;  // Still in the experience (a host restart during a session); the answers are needed to leave.
    }
    withdraw_answers();
  }
}  // namespace tablet_posture::backend

namespace tablet_posture {
  int run_gaming_switch(int argc, char **argv) {
    using namespace backend;
    const std::string_view verb = argc > 2 ? argv[2] : "";
    if (verb != "enter"sv && verb != "leave"sv) {
      return 2;
    }
    const bool enter = verb == "enter"sv;
    const auto &api = gaming_api();
    if (!api.supported || !api.active || !api.set || api.supported() == FALSE) {
      return 3;
    }
    if ((api.active() != FALSE) == enter) {
      return 0;
    }
    if (!api.can_set || api.can_set() == FALSE) {
      return 4;  // No home app chosen for this user.
    }
    request_switch(enter);
    return wait_for_active(enter) ? 0 : 1;
  }
}  // namespace tablet_posture
