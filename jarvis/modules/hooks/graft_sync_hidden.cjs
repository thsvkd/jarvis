// Windows 전용. graft 의 Stop 훅은 GRAFT_TEST_SYNC_RUN 이 가리키는 이 파일을 sync-run.js 대신
// 콘솔 없이(detached) 띄운다. sync-run.js 가 windowsHide 없이 실행하는 `graft build` 는 콘솔이 없는
// 부모 아래에서 보이는 콘솔을 새로 받고, 기본 터미널(Windows Terminal)이 그 콘솔을 창으로 띄운다.
// 원래 sync-run.js 를 windowsHide 로 다시 실행해 숨김 콘솔을 물려주면 창이 뜨지 않는다.
const { execFileSync, spawnSync } = require('child_process');
const path = require('path');

// ponytail: npm 전역 설치만 찾는다. pnpm/bun 전역이면 여기서 못 찾고, graft 는 잠금이 만료될
// 때까지 동기화를 건너뛴다 — 그런 설치가 생기면 탐색 경로를 늘린다.
const root = execFileSync('npm', ['root', '-g'], { encoding: 'utf8', shell: true, windowsHide: true }).trim();
const real = path.join(root, '@nanonets', 'graft', 'dist', 'claude', 'sync-run.js');
spawnSync(process.execPath, [real, ...process.argv.slice(2)], { stdio: 'ignore', windowsHide: true });
