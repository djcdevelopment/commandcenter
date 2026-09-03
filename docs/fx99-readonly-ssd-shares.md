# fx99 read-only SSD shares on OMEN

**Stood up 2026-08-28.** One read-only SMB share per physical SSD in fx99
(`ai-1`, 192.168.12.220), mapped to persistent drive letters on OMEN.

## Map

| OMEN | Share | fx99 path | Physical SSD | Contents |
|------|-------|-----------|--------------|----------|
| `W:` | `\192.168.12.220\WD500` | `/home/derek/drives/WD500` | `sda3` — WDC WDBNCE5000PN 466G, NTFS | old Windows install (Program Files, ProgramData…) |
| `S:` | `\192.168.12.220\SystemSSD` | `/` | `sdb` — Samsung 870 466G, LVM/ext4 | the Ubuntu root filesystem |
| `Y:` | `\192.168.12.220\Betsy` | `/home/derek/drives/Betsy` | `sdc2` — Samsung 850 233G, NTFS | data (91% full) |

Capacities read from OMEN match fx99's own `df` (465.1/415.1, 454.9/359.7,
232.8/21.6 GB) — the mapping is against the real devices, not a stale cache.

## Read-only is enforced twice, not once

1. **Samba layer** — `read only = yes` is set in `[global]`, so it is the
   default every share inherits rather than a per-share opt-in someone can
   forget. Restated per share anyway.
2. **Unix layer** — shares are served as `omenro`, a system account with
   `/usr/sbin/nologin`, no home, and no write bit anywhere on the exported
   trees.

Verified by doing it, not by reading the config: `Set-Content` against all
three drive letters returns `UnauthorizedAccessException`; `Get-ChildItem`
returns real listings.

## Why `omenro` and not `derek`

`/home/derek` is `0750`, so the NTFS mounts beneath it are unreachable to a
foreign account. Rather than add `omenro` to group `derek` (which would grant
read of the entire home directory), it holds a **traverse-only POSIX ACL**:

```
setfacl -m u:omenro:x /home/derek
```

`omenro` can pass *through* the directory to reach the mounts and cannot list
it. `getfacl /home/derek` shows `user:omenro:--x`. If derek's home permissions
are ever reset, this ACL goes with them and the two NTFS shares break — that is
the first thing to check if `W:`/`Y:` start refusing.

## Network exposure

Per `docs/adr#0014` machine lanes ride the home LAN, never the tailnet. `smbd`
is bound with `interfaces = lo 192.168.12.0/24` + `bind interfaces only = yes`,
and `hosts allow` re-states it at the auth layer. NetBIOS is off, SMB1 is off
(`server min protocol = SMB3`), port 445 only.

⚠ **The first configuration was wrong and this is why the bind is written as a
subnet.** Binding by interface name (`interfaces = lo enp9s0`) made `smbd`
listen on that interface's **globally routable IPv6 addresses**
(`2607:fb92:…:445`) as well as the LAN IPv4 — and `ufw` is inactive on fx99, so
nothing behind it would have caught that. Naming the IPv4 subnet excludes the
IPv6 globals, `tailscale0` (100.122.130.124) and the docker bridges
(172.17/172.18) in one stroke, and survives a DHCP lease change within the
subnet, which binding a literal IP would not. Confirm with:

```
ss -tlnp | grep :445      # expect 127.0.0.1 + 192.168.12.220 + [::1] ONLY
```

## The `SystemSSD` share has two quirks

- Its path is `/`, so the other two SSDs — mounted under `/home/derek/drives` —
  **also appear beneath it**. That is one filesystem seen through its root, not
  a second copy of the data.
- `/proc`, `/sys`, `/dev` and `/run` are vetoed. Browsing pseudo-filesystems
  from Explorer hangs the client for no benefit. Samba matches veto patterns by
  name at any depth, so a directory genuinely called `proc` deeper in the tree
  is hidden too — accepted, on a share whose job is browse and rescue.

## Credentials

Stored on OMEN in Credential Manager (`cmdkey /list:192.168.12.220`, type
Domain Password) so the drives reconnect at logon, with a copy at
`C:\Users\derek\.credentials\fx99-smb.txt` (ACL restricted to `OMEN\derek`,
outside the repo). To rotate:

```
ssh derek@192.168.12.220 'sudo smbpasswd -a omenro'
cmdkey /delete:192.168.12.220
cmdkey /add:192.168.12.220 /user:omenro /pass:<new>
```

## Rebuild from scratch

```
sudo apt-get install -y samba acl
sudo useradd -r -M -d /nonexistent -s /usr/sbin/nologin omenro
sudo setfacl -m u:omenro:x /home/derek
sudo smbpasswd -a omenro
# restore /etc/samba/smb.conf (stock Ubuntu original kept at smb.conf.orig)
sudo systemctl enable --now smbd
sudo systemctl mask nmbd samba-ad-dc    # NetBIOS + AD DC deliberately off
```

On OMEN: `net use W: \192.168.12.220\WD500 /persistent:yes` (and `S:`, `Y:`).

## Caveat that outlives this doc

fx99's RTX 2070 SUPER is earmarked for AM4, and `docs/adr#0039` retires the
`fx99-ollama` rung if the card moves. **These shares are independent of that** —
they are disk, not GPU, and survive the card leaving. What they do not survive
is fx99 losing the wired link, which it has swapped with AM4 before.
