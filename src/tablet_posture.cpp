/**
 * @file src/tablet_posture.cpp
 * @brief Client-driven laptop/slate state (see src/tablet_posture.h); platform backends do the actual switching.
 */
// standard includes
#include <condition_variable>
#include <mutex>
#include <thread>

// local includes
#include "src/config.h"
#include "src/logging.h"
#include "src/tablet_posture.h"

using namespace std::literals;

namespace tablet_posture {
  namespace backend {
    /// Switch the host to `state`. Returns false when the platform could not do it. Implemented per platform.
    bool apply(state_e state);

    /// Undo a previous host process' leftovers; with `resync` also put a freshly booted indicator back to laptop. Implemented per platform.
    void recover(bool resync);
  }  // namespace backend

#ifndef _WIN32
  namespace backend {
    bool apply(state_e) {
      return true;
    }

    void recover(bool) {}
  }  // namespace backend
#endif

  namespace {
    constexpr auto retry_delay = 5s;

    std::mutex mutex;
    std::condition_variable wake;
    registry_t registry;
    state_e applied = state_e::laptop;
    std::thread worker;
    bool stopping = false;

    const char *name(state_e state) {
      return state == state_e::slate ? "slate (tablet)" : "laptop (desktop)";
    }

    void worker_main() {
      std::unique_lock lock {mutex};
      std::optional<std::chrono::steady_clock::time_point> laptop_due;
      std::optional<std::chrono::steady_clock::time_point> retry_due;
      while (!stopping) {
        const auto wanted = registry.state();
        const auto now = std::chrono::steady_clock::now();
        if (wanted == applied) {
          laptop_due.reset();
          retry_due.reset();
          wake.wait(lock);
          continue;
        }
        if (retry_due && now < *retry_due) {
          wake.wait_until(lock, *retry_due);
          continue;
        }
        if (wanted == state_e::laptop) {
          if (!laptop_due) {
            laptop_due = now + laptop_grace;
          }
          if (now < *laptop_due) {
            wake.wait_until(lock, *laptop_due);
            continue;
          }
        }
        laptop_due.reset();
        lock.unlock();
        const bool ok = backend::apply(wanted);
        lock.lock();
        if (ok) {
          BOOST_LOG(info) << "Tablet posture: host is now "sv << name(wanted);
          applied = wanted;
          retry_due.reset();
        } else {
          BOOST_LOG(warning) << "Tablet posture: could not switch the host to "sv << name(wanted) << "; retrying"sv;
          retry_due = std::chrono::steady_clock::now() + retry_delay;
        }
      }
    }

    /// Caller holds the mutex.
    void ensure_worker() {
      if (!worker.joinable() && !stopping) {
        worker = std::thread(worker_main);
      }
    }

    void changed() {
      ensure_worker();
      wake.notify_all();
    }
  }  // namespace

  void client_started(const std::string &client, bool touch_primary) {
    if (!config::video.touch_tablet_mode) {
      return;
    }
    std::lock_guard lock {mutex};
    registry.start(client, touch_primary);
    changed();
  }

  void client_updated(const std::string &client, bool touch_primary) {
    if (!config::video.touch_tablet_mode) {
      return;
    }
    std::lock_guard lock {mutex};
    registry.update(client, touch_primary);
    changed();
  }

  void client_stopped(const std::string &client) {
    std::lock_guard lock {mutex};
    registry.stop(client);
    changed();
  }

  void set_override(std::optional<state_e> forced) {
    std::lock_guard lock {mutex};
    registry.set_override(forced);
    BOOST_LOG(info) << "Tablet posture: override "sv << (forced ? name(*forced) : "cleared");
    changed();
  }

  status_t status() {
    std::lock_guard lock {mutex};
    return {applied, registry.state(), registry.override_state(), config::video.touch_tablet_mode, registry.clients()};
  }

  void recover() {
    std::lock_guard lock {mutex};
    backend::recover(config::video.touch_tablet_mode);
  }

  void shutdown() {
    {
      std::lock_guard lock {mutex};
      stopping = true;
    }
    wake.notify_all();
    if (worker.joinable()) {
      worker.join();
    }
    std::lock_guard lock {mutex};
    if (applied != state_e::laptop || registry.state() != state_e::laptop) {
      backend::apply(state_e::laptop);
      applied = state_e::laptop;
    }
  }
}  // namespace tablet_posture
