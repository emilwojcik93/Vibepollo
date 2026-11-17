#pragma once
#include <filesystem>
namespace platf { inline std::filesystem::path appdata() { return std::filesystem::current_path(); } }
