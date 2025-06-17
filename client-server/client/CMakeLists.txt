cmake_minimum_required(VERSION 3.5)

project(aos_http_client)

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++17")
set(CMAKE_INSTALL_PREFIX "${CMAKE_SOURCE_DIR}")

add_executable(${PROJECT_NAME}
    src/main.cpp
)

find_package(Poco REQUIRED Foundation Net)

target_link_libraries(${PROJECT_NAME}
    PocoNet
    PocoFoundation
    ${CMAKE_THREAD_LIBS_INIT}
)

install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION rootfs)
