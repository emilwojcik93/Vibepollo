/**
 * @file src/platform/windows/gaming_posture.cpp
 * @brief Windows backend for the gaming posture (Xbox full screen experience). Placeholder.
 */
// local includes
#include "src/logging.h"
#include "src/tablet_posture.h"

using namespace std::literals;

namespace tablet_posture::backend {
  bool apply_gaming(bool enter) {
    BOOST_LOG(debug) << "Tablet posture: gaming posture "sv << (enter ? "enter"sv : "leave"sv) << " is not implemented yet"sv;
    return false;
  }
}  // namespace tablet_posture::backend
