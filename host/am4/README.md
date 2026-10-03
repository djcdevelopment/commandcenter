# AM4 default profile

Lab configuration `day` has AM4 on `tool-pair`. `am4-vllm.service` is still enabled on AM4, so a reboot brings the 27B back.
Run on AM4 (the orchestrator applies these over ssh):

    install -D host/am4/am4-default-profile.service ~/.config/systemd/user/am4-default-profile.service && systemctl --user daemon-reload && systemctl --user enable am4-default-profile.service
    systemctl --user disable am4-vllm.service
