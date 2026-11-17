/**
 * @file src/tablet_posture.cpp
 * @brief Client-driven laptop/slate state (see src/tablet_posture.h); platform backends do the actual switching.
 */
// standard includes
#include <mutex>

// local includes
#include "src/config.h"
#include "src/logging.h"
#include "src/tablet_posture.h"
#include "src/tablet_posture_policy.h"

using namespace std::literals;

namespace tablet_posture {
  namespace backend {
    /// Switch the host to `state`. Returns false when the platform could not do it. Implemented per platform.
    bool apply(state_e state);

    /// Undo a previous host process' leftovers. Implemented per platform.
    void recover();
  }  // namespace backend

#ifndef _WIN32
  namespace backend {
    bool apply(state_e) {
      return true;
    }

    void recover() {}
  }  // namespace backend
#endif

  namespace {
    std::mutex mutex;
    registry_t registry;
    state_e applied = state_e::laptop;

    const char *name(state_e state) {
      return state == state_e::slate ? "slate (tablet)" : "laptop (desktop)";
    }

    void reconcile() {
      const auto wanted = registry.state();
      if (wanted == applied) {
        return;
      }
      if (backend::apply(wanted)) {
        BOOST_LOG(info) << "Tablet posture: host is now "sv << name(wanted);
        applied = wanted;
      } else {
        BOOST_LOG(warning) << "Tablet posture: could not switch the host to "sv << name(wanted);
      }
    }
  }  // namespace

  void client_started(const std::string &client, bool touch_primary) {
    if (!config::video.touch_tablet_mode) {
      return;
    }
    std::lock_guard lock {mutex};
    registry.start(client, touch_primary);
    reconcile();
  }

  void client_updated(const std::string &client, bool touch_primary) {
    if (!config::video.touch_tablet_mode) {
      return;
    }
    std::lock_guard lock {mutex};
    registry.update(client, touch_primary);
    reconcile();
  }

  void client_stopped(const std::string &client) {
    std::lock_guard lock {mutex};
    registry.stop(client);
    reconcile();
  }

  void recover() {
    std::lock_guard lock {mutex};
    backend::recover();
  }

  void shutdown() {
    std::lock_guard lock {mutex};
    registry = registry_t {};
    reconcile();
  }
}  // namespace tablet_posture
