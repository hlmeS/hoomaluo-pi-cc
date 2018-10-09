# hoomaluo-pi-cc
Repository for the Python code of the Hoomaluo Cool Controls system.


## Crontab

To execute the python script on startup and check on it every so many minutes (e.g. 15), add these by first typing `crontab -e`. Then add these two lines to the end of the file:
```sh
@reboot cd ~/hoomaluo-pi-cc/src-noir && python3 app.py > test.out &
*/15* * * * bash ~/hoomaluo-pi-pm/src-noir/checkpython.sh
```
