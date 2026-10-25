# Kinkaran on Oracle Cloud (Always Free)

The bot, the API, the counter screen and backups run on a free Oracle VM with HTTPS. The shop Mac
only prints: a small **print helper** collects new bills from the cloud and prints them on the HP.

```
Shopkeeper's Telegram ──▶ Telegram ◀── polling ── Oracle VM (Mumbai/Hyderabad)
                                                   kinkaran.service (systemd) · Caddy HTTPS · nightly backup
                                                   https://myshop.duckdns.org ── customer PDF links, counter screen
Shop Mac: print helper ── every 3 s: "anything to print?" (with ACCESS_KEY) ──▶ lp ──▶ HP Smart Tank
```

**What you get:**
- **It survives outages at the shop:** the bot keeps billing when the shop's power or internet is down.
  Printing waits for the Mac and catches up afterwards.
- **A fixed HTTPS address,** so PDF links in customers' messages keep working.
- **Python 3.12,** with logs kept tidy by the system journal.

**Free-tier limits to know** (October 2026):
- **Size:** 2 ARM CPUs and 12 GB RAM, plenty for Kinkaran.
- **A card is needed at sign-up.**
- **Idle reclaim:** Oracle may reclaim a free VM that stays "idle" (CPU, network and memory under 20% for
  7 days), and Kinkaran is mostly idle. Upgrading the account to **Pay As You Go** (it stays free within
  the Always Free limits) is the usual protection. Set a budget alert of ₹1.

Official limits: [docs.oracle.com, Always Free Resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm).

## 1. Create the VM (Oracle console, ~15 min)

1. Sign up at **cloud.oracle.com**. Choose **India West (Mumbai)** or **India South (Hyderabad)** as home
   region. It can't be changed later.
2. **Compute → Instances → Create instance:**
   - Image: **Canonical Ubuntu 24.04** (not Oracle Linux)
   - Shape: **Ampere → VM.Standard.A1.Flex**, **1 OCPU, 6 GB** (leaves room in the free 2 OCPU / 12 GB)
   - Networking: create a new VCN with a **public subnet**, and assign a **public IPv4 address**
   - SSH keys: **Save private key** (e.g. `~/.ssh/oracle.key`, then `chmod 600 ~/.ssh/oracle.key`)

   "Out of capacity" is common for free ARM VMs: try again later or pick another availability domain.
3. Note the **public IP**.

## 2. Open ports 80 and 443 (Oracle console)

**Networking → Virtual cloud networks → your VCN → Security Lists → Default → Add Ingress Rules:**

| Source CIDR | Protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

`setup.sh` opens the same ports in the VM's own firewall. Both are needed.

## 3. A free domain name for HTTPS

1. Go to **duckdns.org** and sign in.
2. Create a subdomain such as `myshop`.
3. Set its IP to the VM's public IP. You now have `myshop.duckdns.org`.

Your own domain works the same way: add an `A` record pointing to the IP.

## 4. Install on the VM

```bash
ssh -i ~/.ssh/oracle.key ubuntu@<VM-IP>
curl -fsSL https://raw.githubusercontent.com/jasimcm/kinkaran/main/deploy/oracle/setup.sh -o setup.sh
sudo DOMAIN=myshop.duckdns.org bash setup.sh
```

This takes about 3 minutes. It sets India time, installs Python, git and Caddy, puts the app in
`/opt/kinkaran/app`, opens the firewall, and installs the services. It doesn't start the bot yet,
because there is no `.env` on the VM.

## 5. Move from the Mac (one command, on the Mac)

```bash
cd ~/kinkaran
SSH_KEY=~/.ssh/oracle.key deploy/oracle/move_from_mac.sh <VM-IP> myshop.duckdns.org
```

1. **Stops Kinkaran on the Mac first.** Only one copy of the bot may poll Telegram, or messages go missing.
2. **Copies `.env`, `bills.json` and `inventory.json`** to the VM, setting `PRINTER=remote` and
   `PUBLIC_URL=https://myshop.duckdns.org`.
3. **Starts the cloud server** and waits until it answers over HTTPS.
4. **Sets up the print helper on the Mac** (`print_agent/agent.env` plus launchd), after checking the
   server, the key and the printer.

If step 3 fails, nothing is lost: the Mac's data is untouched, and `scripts/install_launchd.sh` brings it
all back on the Mac.

## 6. Check it

| Check | How | Expect |
|---|---|---|
| Bot | Send "hi" to the bot | Kinkaran welcome |
| Bill and print | Voice note → "ശരി" | The bill prints at the shop within ~5 s; the bot says "sent to the shop printer" |
| Printer off | Turn the HP off, make a bill | Telegram: "❌ EB-… didn't print at the shop"; say "print EB-…" once it's back on |
| Counter screen | On the Mac: `https://myshop.duckdns.org/?key=<ACCESS_KEY>` once, then `/counter.html` | Shows the newest bill |
| API locked | `https://myshop.duckdns.org/api/bills/latest` from a phone | 403 |
| Survives reboot | `sudo reboot` on the VM | Bot answers again within ~1 min |
| Backup | `ls /opt/kinkaran/backups` the next morning | A copy from 23:30 |

## Everyday commands (on the VM)

```bash
journalctl -u kinkaran -f                  # live log
sudo systemctl restart kinkaran            # restart after editing /opt/kinkaran/app/.env
sudo DOMAIN=myshop.duckdns.org bash /opt/kinkaran/app/deploy/oracle/setup.sh   # update to the newest code
ls /opt/kinkaran/backups                   # nightly backups (30 kept)
```

On the Mac, `tail -f ~/kinkaran/logs/printer.log` shows the print helper.

## Backups off the VM

The nightly backups live on the VM's own disk. Copy them off now and then:

```bash
scp -i ~/.ssh/oracle.key -r ubuntu@<VM-IP>:/opt/kinkaran/backups ~/KinkaranBackups-cloud
```

Oracle Object Storage (20 GB free) is another option.

## Going back to the Mac

1. `ssh ubuntu@<VM-IP> sudo systemctl disable --now kinkaran`
2. Copy `/opt/kinkaran/app/backend/bills.json` back to the Mac.
3. On the Mac, set `PRINTER` back to the printer name in `.env`, then run:

   ```bash
   scripts/install_print_agent.sh --uninstall
   scripts/install_launchd.sh
   ```
