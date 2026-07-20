/**
 * G2B 알리미 Cron Worker
 * 평일 09:30 KST 정각에 GitHub Actions 이메일 워크플로우를 트리거합니다.
 *
 * 환경변수 (Cloudflare Dashboard → Settings → Variables → Secrets):
 *   GITHUB_TOKEN : GitHub Personal Access Token (repo scope)
 */

const REPO     = 'jscompany909-cpu/g2b-dashboard';
const WORKFLOW = 'notify.yml';

export default {
  // ── Cron 트리거 (wrangler.toml 스케줄에서 자동 실행) ──────────────
  async scheduled(event, env, ctx) {
    await triggerWorkflow(env);
  },

  // ── HTTP 핸들러 (수동 테스트: POST /trigger) ──────────────────────
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === 'POST' && url.pathname === '/trigger') {
      const result = await triggerWorkflow(env);
      return new Response(JSON.stringify(result), {
        status: result.ok ? 200 : 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    return new Response(
      JSON.stringify({ status: 'G2B Cron Worker 동작 중', schedule: '평일 09:30 KST' }),
      { headers: { 'Content-Type': 'application/json' } }
    );
  },
};

async function triggerWorkflow(env) {
  const now = new Date().toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });
  console.log(`[G2B Cron] 실행: ${now}`);

  const res = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
    {
      method: 'POST',
      headers: {
        Authorization:  `Bearer ${env.GITHUB_TOKEN}`,
        Accept:         'application/vnd.github+json',
        'User-Agent':   'G2B-Cron-Worker/1.0',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main' }),
    }
  );

  if (res.ok) {
    console.log(`[G2B Cron] 이메일 워크플로우 트리거 성공 (${res.status})`);
    return { ok: true, status: res.status, time: now };
  } else {
    const text = await res.text();
    console.error(`[G2B Cron] 실패: ${res.status} — ${text}`);
    return { ok: false, status: res.status, error: text, time: now };
  }
}
