#include <iostream>
#include <chrono>
#include <thread> 

int main() {
    while (true) 
    {
        std::cout << "[AOS_Edge] Hello, World!" << std::endl;
        
        std::this_thread::sleep_for(std::chrono::seconds(5));
    }
    
    return 0;
}
