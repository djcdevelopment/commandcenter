# AM4 default profile

Lab configuration `day` has AM4 on `tool-pair`. `am4-vllm.service` is still enabled on AM4, so a reboot brings the 27B back.
From the flash checkout on OMEN (AM4 is `10.44.0.2` over the direct cable; linger is on, user units live in
`~/.config/systemd/user/`). Nothing starts now; the next boot comes up on tool-pair:

    scp host/am4/am4-default-profile.service 10.44.0.2:.config/systemd/user/am4-default-profile.service
    ssh 10.44.0.2 'systemctl --user daemon-reload && systemctl --user enable am4-default-profile.service && systemctl --user disable am4-vllm.service'

`am4-profile dense-tp2` still starts `am4-vllm.service` by hand for MemSplice (configuration `memsplice`).
