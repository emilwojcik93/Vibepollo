/**
 * @file src/tablet_posture_policy.h
 * @brief Pure rules for the host's laptop/slate (tablet) state.
 *
 * Windows decides "tablet posture" from a laptop/slate indicator, the way a convertible laptop
 * reports its keyboard being folded back or detached. The host plays that indicator for the
 * streaming clients: while at least one active client is touch-primary (tablet, handheld,
 * phone, or a touch device without a declared posture) the host is a slate; with no such
 * client, or when the client reports a laptop or desktop posture (keyboard attached), it is a
 * laptop. This header holds the bookkeeping so it can be tested without Windows.
 */
#pragma once

#include <cstddef>
#include <map>
#include <string>

namespace tablet_posture {
  enum class state_e {
    laptop,  ///< Keyboard attached or folded out: Windows desktop behaviour.
    slate,  ///< Keyboard detached or folded back: Windows tablet behaviour.
  };

  /// Active clients and whether each one is touch-primary. A client may hold several sessions.
  class registry_t {
  public:
    /// A session of `client` started. Returns true when the host state changed.
    bool start(const std::string &client, bool touch_primary) {
      auto &entry = clients_[client];
      ++entry.sessions;
      entry.touch_primary = touch_primary;
      return refresh();
    }

    /// The client's posture changed while it streams. Unknown clients are ignored. Returns true when the host state changed.
    bool update(const std::string &client, bool touch_primary) {
      const auto it = clients_.find(client);
      if (it == clients_.end()) {
        return false;
      }
      it->second.touch_primary = touch_primary;
      return refresh();
    }

    /// A session of `client` ended. Returns true when the host state changed.
    bool stop(const std::string &client) {
      const auto it = clients_.find(client);
      if (it == clients_.end()) {
        return false;
      }
      if (--it->second.sessions <= 0) {
        clients_.erase(it);
      }
      return refresh();
    }

    bool contains(const std::string &client) const {
      return clients_.find(client) != clients_.end();
    }

    std::size_t size() const {
      return clients_.size();
    }

    state_e state() const {
      return state_;
    }

  private:
    struct entry_t {
      int sessions = 0;
      bool touch_primary = false;
    };

    bool refresh() {
      state_e wanted = state_e::laptop;
      for (const auto &[client, entry] : clients_) {
        if (entry.touch_primary) {
          wanted = state_e::slate;
          break;
        }
      }
      const bool changed = wanted != state_;
      state_ = wanted;
      return changed;
    }

    std::map<std::string, entry_t> clients_;
    state_e state_ = state_e::laptop;
  };
}  // namespace tablet_posture
