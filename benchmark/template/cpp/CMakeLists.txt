cmake_minimum_required(VERSION 3.5)

project(benchmark_template CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_INSTALL_PREFIX "${CMAKE_SOURCE_DIR}")

add_executable(${PROJECT_NAME} src/benchmark_template.cpp)

find_package(Poco REQUIRED Foundation Net)

target_link_libraries(${PROJECT_NAME}
    PocoNet
    PocoFoundation
)

install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION rootfs)
