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
#include <optional>
#include <string>
#include <vector>

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

    /// State the clients ask for, ignoring any override.
    state_e client_state() const {
      return client_state_;
    }

    /// The state to apply: the override when set, else what the clients ask for.
    state_e state() const {
      return override_.value_or(client_state_);
    }

    /// Force a state regardless of the clients (simulates the keyboard being detached or attached). nullopt returns control to the clients.
    void set_override(std::optional<state_e> forced) {
      override_ = forced;
    }

    std::optional<state_e> override_state() const {
      return override_;
    }

    struct client_info_t {
      std::string id;
      int sessions;
      bool touch_primary;
    };

    std::vector<client_info_t> clients() const {
      std::vector<client_info_t> out;
      for (const auto &[id, entry] : clients_) {
        out.push_back({id, entry.sessions, entry.touch_primary});
      }
      return out;
    }

  private:
    struct entry_t {
      int sessions = 0;
      bool touch_primary = false;
    };

    bool refresh() {
      const auto before = state();
      client_state_ = state_e::laptop;
      for (const auto &[client, entry] : clients_) {
        if (entry.touch_primary) {
          client_state_ = state_e::slate;
          break;
        }
      }
      return state() != before;
    }

    std::map<std::string, entry_t> clients_;
    state_e client_state_ = state_e::laptop;
    std::optional<state_e> override_;
  };
}  // namespace tablet_posture
