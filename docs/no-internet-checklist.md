# No internet on a Windows PC

Work from the simplest cause to the hardest. Stop as soon as the problem is fixed, and write down what fixed it.

You can run `python itcheck.py net` first. It tells you which step below to start from.

## 1. Check the basics

- Is the network cable plugged in at both ends, or is Wi-Fi turned on? Is airplane mode off?
- Do other devices on the same network have internet? If they do not either, the problem is the router, modem, or provider, not this PC.
- Restart the PC. It fixes more problems than it should.

## 2. Look at the network settings

Open Command Prompt and run:

```
ipconfig /all
```

- An address starting with `169.254` means the PC asked for an address and got no answer. Check the router and cable.
- No address at all means the adapter is disabled or the cable or Wi-Fi is not connected.
- A normal address (for example `192.168.x.x`) means the PC is connected to the local network. Go to step 3.

## 3. Test step by step

```
ping 1.1.1.1
ping example.com
```

| Result | Meaning | Next step |
| --- | --- | --- |
| First ping fails | No route to the internet | Restart the router and modem, check the provider |
| First works, second fails | DNS problem | Step 4 |
| Both work, browser fails | Browser, proxy, or firewall problem | Step 5 |

## 4. Fix DNS

```
ipconfig /flushdns
```

If that does not help, set the DNS server manually: Settings, Network and Internet, adapter properties, edit DNS, then use `1.1.1.1` and `8.8.8.8`.

## 5. Reset the network stack

Open Command Prompt as administrator and run:

```
ipconfig /release
ipconfig /renew
netsh winsock reset
netsh int ip reset
```

Restart the PC afterward. The last two commands change network settings, so use them only after the earlier steps have not worked.

## 6. If it still fails

- Check for a proxy: Settings, Network and Internet, Proxy. Turn off any proxy you did not set up on purpose.
- Update or reinstall the network adapter driver in Device Manager.
- Try another cable or network port to rule out hardware.
- Run `tracert 1.1.1.1` to see where the connection stops.

## What to write in the ticket

- What the user reported and when it started
- What `ipconfig /all` showed
- Which step fixed it, or which steps were tried
- Whether other devices were affected
