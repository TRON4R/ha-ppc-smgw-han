# Network Setup: Connecting Home Assistant to the SMGW

The PPC Smart Meter Gateway is typically permanently configured to use a fixed IP (e.g. `192.168.100.100` or `192.168.1.200`) — typically, this cannot be changed. Home Assistant typically runs on your router's local network, e.g. on an address like `192.168.2.12`. Since these two network ranges cannot communicate directly, the easiest and most elegant solution is to assign your Home Assistant server a second IP address from the IP range of the SMGW (e.g. `192.168.100.x` or `192.168.1.x`). For the following instructions, I'll simply assume the IP of the SMGW is `192.168.100.100` for the rest of these instructions.

> [!NOTE]
> The network settings below only exist on **Home Assistant OS** and **Supervised**. **HA Container** (Docker, e.g. in a NAS's container manager) and **HA Core** don't have them; there you set up the additional IP address on the host system.

## Which option fits?

- **[Option A](#option-a) – a second IP address on the existing network interface:** always works, even if the machine has only one network port (e.g. a Raspberry Pi). The interface has to be switched to _Static_ for this.
- **[Option B](#option-b) – a dedicated network interface for the SMGW:** recommended if Home Assistant runs in a virtual machine (e.g. on Proxmox) or the machine has a second network port. The existing interface stays on _Automatic_: you can't accidentally configure away your access to HA, and after a router or address-range change (e.g. when switching from DSL to fibre) HA keeps getting its address automatically.

## <a id="option-a"></a>Option A: A second IP address on the existing interface

1. _**Settings → System → Network**_
2. Open the _Configure network interfaces_ section and expand _IPv4_
3. Select _Static_ (if not already active — this is required because _Automatic_ (DHCP) and multiple IP addresses are mutually exclusive in HA). Note down the current IP address first.
4. Click _+ Add address_
5. In the new field (showing 0.0.0.0) enter the new _IP address_, e.g. `192.168.100.12`
   - The last number (here `12`) can be anything you like, as long as that IP is not already taken in the `192.168.100.x` range. Normally this range should be empty except for `192.168.100.100` (the SMGW itself).
   - Be careful not to accidentally fill in the wrong field and cut off your own access.
6. Check that the _Netmask_ is `255.255.255.0` (should be correct automatically)
7. _**Save**_

**When done, it should look like this:**

![Configure network interfaces in Home Assistant](network-setup.png)

**That's all there is to it.** Your Home Assistant server can now talk directly to the SMGW — no complicated routes, VLANs, or restructuring of your entire home network required.
All that's left is to start the SMGW integration, which should then successfully connect to the SMGW.

Because the interface is now set to _Static_, HA no longer gets its address from the router. If you later change your router or address range, you have to adjust the address here by hand, otherwise HA becomes unreachable. If you want to avoid that, use [Option B](#option-b).

## <a id="option-b"></a>Option B: A dedicated network interface for the SMGW

_Idea and screenshot: [@ptar](https://github.com/ptar), see [Discussion #71](https://github.com/TRON4R/ha-ppc-smgw-han/discussions/71). Thank you!_

Example Proxmox:

1. In Proxmox, select the VM → _Hardware_ → _Add_ → _Network Device_. Choose the same _Bridge_ as the existing network device (usually `vmbr0`) and _VirtIO_ as the _Model_. The hardware list then shows a second network device (`net1`):

   ![Second network device in Proxmox](network-setup-proxmox-network-device.png)
2. Restart the VM.
3. In Home Assistant, open _**Settings → System → Network**_. _Configure network interfaces_ now shows two tabs, e.g. `enp0s18` and `enp0s19`. You can tell the existing interface by its IP address (note it down beforehand); the new one is usually the one with the higher number.
4. On the **new** interface only:
   - _IPv4_: select _Static_, enter e.g. `192.168.100.12` as the _IP address_ and `255.255.255.0` as the _Netmask_.
   - _Gateway address_: enter the SMGW's address, i.e. `192.168.100.100`. HA doesn't need a gateway to reach the SMGW, but the field is mandatory. If left empty, saving fails with `expected IPv4Address for dictionary value @ data['ipv4']['gateway']. Got None`.
   - _DNS servers_: leave empty.
   - _IPv6_: select _Disabled_ — there is no IPv6 in the SMGW's network.
   - _**Save**_
5. Leave the existing interface alone; it stays on _Automatic_.
6. Briefly check that HA can still reach the internet, e.g. that HACS loads its list. The new interface's gateway is a second way out; normally the existing interface keeps priority. If it doesn't, use Option A.

**This is what the new interface looks like when done:**

![Dedicated network interface for the SMGW in Home Assistant](network-setup-second-interface.png)

Other hypervisors (e.g. Synology VMM, QNAP Virtualization Station, VirtualBox) work the same way: add another network adapter to the VM, restart the VM and continue with step 3. If the machine has a second physical network port, connect it to the switch and continue with step 3 as well.

## Notes

- Of course, the HAN port of the SMGW must be connected via LAN cable to the same switch that the Home Assistant server is connected to. If Home Assistant is running in a virtual machine (e.g. on a NAS such as Synology or QNAP), the IP address or IP range of the NAS (the hosting device) itself does not matter. What matters is only that the Home Assistant instance has this additional IP address in the SMGW's IP range active (as achieved by the instructions above).
- A restart of Home Assistant may be required after saving for the change to take effect.
- Depending on your setup (VM, host system), a full reboot of the virtual machine or the host computer may be needed for the new IP to become active.
- The new IP does **not** need to be entered as the SMGW URL — that stays `https://192.168.100.100/cgi-bin/hanservice.cgi`. The second IP simply allows Home Assistant to reach `192.168.100.100` at all, without any complicated routing tables.
- If you also want to access the SMGW web interface from your PC's browser, you'll need to give your PC a second IP in the `192.168.100.x` range as well — different from both `.100` (SMGW) and the one assigned to Home Assistant. Any AI assistant (ChatGPT, Claude, Gemini, etc.) can walk you through how to do this on Windows, macOS, or Linux.
- All my attempts to embed the SMGW web interface inside Home Assistant (e.g. as an iFrame card, to avoid giving my PC a second IP) have failed. The BSI has locked down the SMGW completely. As someone once aptly put it: these gateways would successfully repel an attack from Mars. iFrame embedding is blocked, stripping the `X-FRAME-OPTIONS: deny` header via an Nginx proxy doesn't help either — you authenticate successfully and then get immediately kicked out due to session cookies and other security measures. The SMGW doesn't even respond to a simple `ping`. **If you find a solution that works reliably inside HA, please let me know!**
