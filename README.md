#AlpmFs
Display installed packages with pacman in directories

## install

```
yay -S python-pyfuse3 --asdeps    # 7 packages for 3.5 Mo
yay -S pyalpm --asdeps
```

## run

```
./pacmanfs.py test
# or absolute path
./pacmanfs.py ~/pacman
```

## exit
CTRL + C

---

## test

```
ls -l ~/pacman
ls -l ~/pacman/zlib/glibc.dep/filesystem.dep/
cat ~/pacman/zlib/glibc.dep/filesystem.dep/filesystem.name
```
