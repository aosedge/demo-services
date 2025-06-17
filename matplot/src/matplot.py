import importlib.metadata
import time

try:
    import matplotlib.pyplot as plt
    print("Matplotlib is installed.")
except ImportError:
    print("Matplotlib is not installed.")
    exit()

x = [1, 2, 3, 4, 5]
y = [2, 3, 5, 7, 11] 

plt.figure(figsize=(8, 5))
plt.plot(x, y, marker='o', color='royalblue', linestyle='-', linewidth=2, label='Prime Numbers')

plt.title('Line Plot Example')
plt.xlabel('X Axis')
plt.ylabel('Y Axis')

plt.legend()

while True:
    print("Plot has been successfully created.")
    time.sleep(5)
