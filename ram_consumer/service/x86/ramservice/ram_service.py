import argparse
import time
import math

def alloc_chunk_memory(limit: int):
    ONE_MB = 10 ** 6
    MEGA_STR = ' ' * ONE_MB
    i = 0
    ar = []

    for i in range(limit):
        try:
            ar.append(MEGA_STR + str(i))
            print(f"Allocated another chunk{i} memory")
        except MemoryError:
            break
        time.sleep(1)
    return ar

def get_etc_hostnames(host : str):
    with open('/etc/hosts', 'r') as f:
        hostlines = f.readlines()
    hostlines = [line.strip() for line in hostlines
                 if not line.startswith('#') and line.strip() != '']

    for line in hostlines:
        hostnames = line.split('#')[0].split()[1:]
        if host == hostnames[0]:
            return True

    return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-host", "--hostname", help = "Host name", required=True)
    parser.add_argument("-m", "--memory", help = "How much use memory(MB)", required=True)

    args = vars(parser.parse_args())

    ar = []

    if get_etc_hostnames(args["hostname"]):
        ar = alloc_chunk_memory(int(args["memory"]))
    while True:
        print("Service continues to work!!!1")
        time.sleep(1)

if __name__ == "__main__":
    main()
