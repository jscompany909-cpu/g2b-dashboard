/**
 * G2B 통합 파이프라인 정시 트리거 Worker
 * 평일 08:00 KST 정각에 GitHub Actions pipeline.yml(수집 → 대시보드 → 메일)을 workflow_dispatch로 실행합니다.
 * GitHub 자체 예약(schedule)은 수 시간 지연·누락이 잦아 백업으로만 둡니다.
 *
 * 환경변수 (Cloudflare Dashboard → Settings → Variables → Secrets):
 *   GITHUB_TOKEN   : GitHub Personal Access Token (repo scope)
 *   TRIGGER_SECRET : POST /trigger 엔드포인트 인증 토큰
 */

const REPO     = 'jscompany909-cpu/g2b-dashboard';
const WORKFLOW = 'pipeline.yml';

export default {
  // ── Cron 트리거 (wrangler.toml 스케줄에서 자동 실행) ──────────────
  async scheduled(event, env, ctx) {
    await triggerWorkflow(env);
  },

  // ── HTTP 핸들러 (수동 테스트: POST /trigger) ──────────────────────
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // Authenticate POST /trigger requests
    if (request.method === 'POST' && url.pathname === '/trigger') {
      const authToken = request.headers.get('X-Trigger-Token');
      if (!env.TRIGGER_SECRET || authToken !== env.TRIGGER_SECRET) {
        return new Response(JSON.stringify({ error: 'Unauthorized' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      const result = await triggerWorkflow(env);
      return new Response(JSON.stringify(result), {
        status: result.ok ? 200 : 500,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    return new Response(
      JSON.stringify({ status: 'G2B Cron Worker 동작 중', workflow: WORKFLOW, schedule: '평일 08:00 KST' }),
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
    console.log(`[G2B Cron] ${WORKFLOW} 트리거 성공 (${res.status})`);
    return { ok: true, status: res.status, time: now };
  } else {
    const text = await res.text();
    console.error(`[G2B Cron] 실패: ${res.status} — ${text}`);
    return { ok: false, status: res.status, error: text, time: now };
  }
}
