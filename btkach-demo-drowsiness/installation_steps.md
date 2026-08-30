mount -o remount,rw /


copy requirement.txt to VM
run pip3 install -r req.txt



clean up: 
 systemctl stop aos.target

systemctl | grep aos

 rm -rf /var/aos/workdirs/sm
 
systemctl start aos.target