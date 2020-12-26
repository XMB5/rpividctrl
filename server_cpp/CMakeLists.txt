cmake_minimum_required(VERSION 3.13)
project(rpividctrl_server_cpp)
set(CMAKE_CXX_STANDARD 14)

# I think glib always leaks 8192 bytes, so we can ignore that
# https://gstreamer-bugs.narkive.com/eAIa0Fa8/bug-681913-new-gst-init-check-cause-memory-leak
set (SANITIZERS "-fsanitize-recover=all -fsanitize=address -fsanitize=leak -fsanitize=undefined -g")

set (CMAKE_CXX_FLAGS_DEBUG "${CMAKE_CXX_FLAGS_DEBUG} ${SANITIZERS}")
set (CMAKE_LINKER_FLAGS_DEBUG "${CMAKE_LINKER_FLAGS_DEBUG} ${SANITIZERS}")

find_package(PkgConfig)

pkg_check_modules(deps REQUIRED IMPORTED_TARGET gstreamer-1.0 glib-2.0)

add_executable(rpividctrl_server_cpp main.cpp SocketManager.cpp SocketManager.h Message.cpp Message.h)
target_link_libraries(rpividctrl_server_cpp PkgConfig::deps)