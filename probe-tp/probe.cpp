#include <cstring>
#include <iostream>
#include "src/tablet_posture_policy.h"
namespace tablet_posture::backend { bool apply(state_e state); void recover(); }
int main(int argc, char **argv) {
  if (argc < 2) { std::cerr << "usage: tablet-probe slate|laptop|recover\n"; return 2; }
  if (!std::strcmp(argv[1], "recover")) { tablet_posture::backend::recover(); return 0; }
  const bool ok = tablet_posture::backend::apply(!std::strcmp(argv[1], "slate") ? tablet_posture::state_e::slate : tablet_posture::state_e::laptop);
  std::cout << (ok ? "ok" : "FAILED") << "\n"; return ok ? 0 : 1;
}
