cmake_minimum_required(VERSION 3.5)

project(aos_http_server)

set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++17")
set(CMAKE_INSTALL_PREFIX "${CMAKE_SOURCE_DIR}")

add_executable(${PROJECT_NAME}
    src/handlers/HTTPHandlerFactory.cpp
    src/handlers/HealthCheck.cpp
    src/main.cpp
    src/Server.cpp
)

find_package(Poco REQUIRED Foundation Util Net XML JSON)

target_link_libraries(${PROJECT_NAME}
    PocoUtil
    PocoNet
    PocoXML
    PocoFoundation
    PocoJSON
    ${CMAKE_THREAD_LIBS_INIT}
)

install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION rootfs)
