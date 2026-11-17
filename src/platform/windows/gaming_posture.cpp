/**
 * @file src/platform/windows/gaming_posture.cpp
 * @brief Windows backend for the gaming posture, the Xbox full screen experience (see src/tablet_posture.h).
 *
 * Windows calls the Xbox full screen experience ("Xbox mode") its gaming posture. The shell's
 * GamingPostureController enters and leaves it, and gamemode.dll exposes the state through
 * IsGamingFullScreenExperienceSupported/Active and a switch, SetGamingFullScreenExperience. The switch
 * is refused for everyone but the configured gaming home app, the Game Bar and shell components
 * (IGamingPostureBroker::CanSetGamingPosture compares the caller's package identity and otherwise
 * asks for the shellExperience capability), so a host service gets E_ACCESSDENIED from it.
 *
 * What Windows offers every user is the Win+F11 shortcut, handled by the same controller. This
 * backend therefore never toggles blindly: it reads the state, uses the switch when Windows allows
 * it, otherwise presses the shortcut only when the state differs, and then confirms the new state.
 * When the experience is not supported, has no home app, or does not change, the caller keeps the
 * host in tablet mode.
 *
 * Entering and leaving ask for confirmation once. Windows remembers the answers under
 * GamingConfiguration\SystemDialogResults (user hive first, then the machine hive). The answers are
 * supplied in the machine hive only while the host switches and removed afterwards; a journal file
 * lets a restarted host remove them after a crash. A choice the user made is never touched.
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

    /// The confirmations Windows shows around a switch on a PC that is not itself a handheld.
    constexpr std::array<const wchar_t *, 3> dialog_names {
      L"EnterGamingPostureConfirmation_NoReboot",
      L"PostEntryGamingPostureConfirmation",
      L"ExitGamingPostureConfirmation_Minimal",
    };

    constexpr DWORD dialog_accepted = 1;

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
      if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, dialog_results_key, 0, KEY_SET_VALUE, &key) == ERROR_SUCCESS) {
        for (const auto name : dialog_names) {
          if (std::find(supplied.begin(), supplied.end(), narrow(name)) == supplied.end()) {
            continue;
          }
          const auto status = RegDeleteValueW(key, name);
          clean = clean && (status == ERROR_SUCCESS || status == ERROR_FILE_NOT_FOUND);
        }
        RegCloseKey(key);
      }
      if (clean) {
        std::filesystem::remove(journal_path(), ec);
      } else {
        BOOST_LOG(warning) << "Gaming posture: could not remove the confirmation answers; they stay journaled for the next start"sv;
      }
    }

    /// Answer the switch confirmations in the machine hive where no answer exists yet, journaling each one first.
    void supply_answers() {
      HKEY key = nullptr;
      if (RegCreateKeyExW(HKEY_LOCAL_MACHINE, dialog_results_key, 0, nullptr, 0, KEY_QUERY_VALUE | KEY_SET_VALUE, nullptr, &key, nullptr) != ERROR_SUCCESS) {
        BOOST_LOG(warning) << "Gaming posture: could not open the confirmation answers; Windows may ask before switching"sv;
        return;
      }
      for (const auto name : dialog_names) {
        if (RegQueryValueExW(key, name, nullptr, nullptr, nullptr, nullptr) == ERROR_SUCCESS) {
          continue;  // Already answered, by the administrator or by an earlier switch of ours.
        }
        std::ofstream(journal_path(), std::ios::app) << narrow(name) << '\n';
        RegSetValueExW(key, name, 0, REG_DWORD, reinterpret_cast<const BYTE *>(&dialog_accepted), sizeof(dialog_accepted));
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
      // Reserved by Windows for its gaming home app and shell components; used when it is granted.
      switched = SUCCEEDED(api.set(enter ? TRUE : FALSE)) && wait_for_active(enter);
    }
    if (!switched) {
      if (!press_shortcut()) {
        BOOST_LOG(warning) << "Gaming posture: could not send the Win+F11 shortcut"sv;
      }
      switched = wait_for_active(enter);
    }

    if (!switched && enter) {
      BOOST_LOG(warning) << "Gaming posture: Windows did not enter the Xbox full screen experience. "
                            "Check that the Xbox app is installed and Xbox mode is available on this host."sv;
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
