cmake_minimum_required(VERSION 3.22)

project(rtps-writer CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include("${CMAKE_CURRENT_SOURCE_DIR}/../src/rtps-service.cmake")

add_rtps_service(writer)
