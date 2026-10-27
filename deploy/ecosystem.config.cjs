// pm2 로 jarvis 를 띄운다.
//   install -Dm644 deploy/pit5.toml ~/.config/jarvis/config.toml   # 기기별 설정(없으면 중립 기본값)
//   pm2 start deploy/ecosystem.config.cjs && pm2 save
// 부팅 시 자동 시작은 `pm2 startup` 이 만든 pm2-<user>.service 가 `pm2 resurrect` 로 맡는다(README 참고).
const path = require("path");

module.exports = {
  apps: [
    {
      name: "jarvis",
      script: "/usr/bin/python3",
      args: "-m jarvis",
      cwd: path.resolve(__dirname, ".."),
      interpreter: "none",
      autorestart: true,
      exp_backoff_restart_delay: 2000,
      kill_timeout: 8000, // SIGINT 를 받으면 히스토리를 저장하고 끝낸다
      max_memory_restart: "300M",
      time: true,
      env: {
        PYTHONUNBUFFERED: "1",
        // docker·tailscale·vcgencmd·ip·systemctl 을 부른다. pm2 데몬의 PATH 에 기대지 않는다.
        PATH: "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/linuxbrew/.linuxbrew/bin",
        XDG_RUNTIME_DIR: "/run/user/1000", // systemctl --user
        JARVIS_PORT: "9300",
      },
    },
  ],
};
