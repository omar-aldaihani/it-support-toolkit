# Slow Windows PC

Find out when it is slow (always, only at startup, only in one program) before changing anything.

## 1. Check what is using resources

Open Task Manager (Ctrl + Shift + Esc) and look at the Processes tab.

- Sort by CPU, Memory, and Disk. One program using almost everything is the likely cause.
- Disk at 100% on an older hard drive is a common sign of a drive that is too slow, nearly full, or failing.
- Memory above about 90% means the PC needs fewer programs open or more RAM.

## 2. Free up disk space

Run the tool to see how full the drive is:

```
python itcheck.py disk
```

Below 10% free space slows Windows down. Use Settings, System, Storage to remove temporary files and uninstall programs nobody uses.

## 3. Reduce startup programs

In Task Manager, open the Startup apps tab. Disable programs that do not need to start with Windows. Disable, do not uninstall, so the change is easy to undo.

## 4. Install updates

Run Windows Update and update the drivers for graphics and network. Restart afterward.

## 5. Check for malware

Run a full scan with Windows Security (Virus and threat protection, Scan options, Full scan). Remove browser extensions the user does not recognise.

## 6. Check system files and the drive

Open Command Prompt as administrator:

```
sfc /scannow
DISM /Online /Cleanup-Image /RestoreHealth
chkdsk C: /scan
```

These can take a while. Let them finish.

## 7. If it is still slow

- Replace a hard drive with a solid-state drive (SSD) if the PC still uses an old hard drive. This is often the biggest improvement for an older machine.
- Add RAM if memory stays full during normal work.
- Back up the user's files before a reset or reinstall, and confirm the backup opens.

## What to write in the ticket

- When the slowness happens and what the user was doing
- What Task Manager showed
- Free disk space before and after
- What changed performance, and what did not
