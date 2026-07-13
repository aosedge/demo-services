cmake_minimum_required(VERSION 3.5)

project(load-test C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_INSTALL_PREFIX "${CMAKE_SOURCE_DIR}")

add_executable(${PROJECT_NAME} src/main.c)

install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION rootfs)
