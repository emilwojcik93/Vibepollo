/**
 * @file src/tablet_posture.h
 * @brief Host laptop/slate state driven by the streaming clients' posture.
 *
 * Windows only switches to tablet behaviour (touch keyboard on text focus, tablet taskbar) when it
 * believes the machine is a convertible whose keyboard is folded back or detached. A virtual
 * display host is neither, so the host reports that state itself, through Windows' own laptop/slate
 * indicator interface, whenever a touch-primary client streams. Clients drive it: `clientPosture`
 * at launch and resume, and the `/posture` endpoint while streaming (keyboard attached, detached,
 * flipped, flipped back). Controlled by the `touch_tablet_mode` option. Other platforms: no-ops.
 *
 * Switching to slate happens at once; switching back to laptop waits a few seconds so a resume or
 * reconnect (which ends one session and starts the next) does not flicker the shell. All Windows work
 * happens on a worker thread, never on a streaming or HTTP thread.
 */
#pragma once

#include <chrono>
#include <optional>
#include <string>
#include <vector>

#include "tablet_posture_policy.h"

namespace tablet_posture {
  /// Delay before the host returns to laptop after the last touch-primary client left.
  inline constexpr std::chrono::milliseconds laptop_grace {3000};

  /// A session of `client` started. `touch_primary` is the client's current touch class.
  void client_started(const std::string &client, bool touch_primary);

  /// The client's posture changed during a session. Ignored when the client has no active session.
  void client_updated(const std::string &client, bool touch_primary);

  /// A session of `client` ended. The last touch-primary client leaving puts the host back to laptop.
  void client_stopped(const std::string &client);

  /// Force the state regardless of clients (a simulated keyboard detach/attach); nullopt gives control back to the clients.
  void set_override(std::optional<state_e> forced);

  struct status_t {
    state_e applied;  ///< What Windows was last told.
    state_e wanted;  ///< What the clients (or the override) ask for.
    std::optional<state_e> override_state;
    bool enabled;  ///< touch_tablet_mode
    std::vector<registry_t::client_info_t> clients;
  };

  status_t status();

  /// At host start: undo whatever a previous host process left behind after a crash.
  void recover();

  /// At host stop: put the host back to laptop and stop the worker.
  void shutdown();
}  // namespace tablet_posture
