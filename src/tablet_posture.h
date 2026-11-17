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
 */
#pragma once

#include <string>

namespace tablet_posture {
  /// A session of `client` started. `touch_primary` is the client's current touch class.
  void client_started(const std::string &client, bool touch_primary);

  /// The client's posture changed during a session. Ignored when the client has no active session.
  void client_updated(const std::string &client, bool touch_primary);

  /// A session of `client` ended. The last touch-primary client leaving puts the host back to laptop.
  void client_stopped(const std::string &client);

  /// At host start: undo whatever a previous host process left behind after a crash.
  void recover();

  /// At host stop: put the host back to laptop.
  void shutdown();
}  // namespace tablet_posture
