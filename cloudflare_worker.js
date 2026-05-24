/**
 * Cloudflare Email Worker — 接收邮件并提取 Serper 验证链接
 *
 * 部署步骤:
 * 1. Cloudflare Dashboard → Workers & Pages → Create → Create Worker
 * 2. 粘贴本文件内容 → Deploy
 * 3. Worker Settings → Bindings → Add KV Namespace → 变量名: EMAILS
 * 4. Email Routing → Catch-all → Route to Worker → 选择这个 Worker
 *
 * 工作原理:
 *   邮件到达 → Worker 提取验证链接 → 存入 KV（key=收件地址）
 *   Python 脚本通过 Cloudflare KV API 读取验证链接
 *   KV 条目 10 分钟后自动过期删除
 */

export default {
  async email(message, env) {
    try {
      // 读取完整邮件内容
      const rawEmail = await new Response(message.raw).text();

      // 提取 Serper 验证链接
      // 邮件可能使用 quoted-printable 编码，先解码 =3D → =
      const decoded = rawEmail.replace(/=3D/g, '=').replace(/=\r?\n/g, '');
      const linkMatch = decoded.match(
        /https:\/\/serper\.dev\/confirm-email\?token=[a-zA-Z0-9_\-\.%]+/
      );

      if (linkMatch && env.EMAILS) {
        const recipient = message.to.toLowerCase();
        const data = JSON.stringify({
          link: linkMatch[0],
          from: message.from,
          to: recipient,
          timestamp: Date.now(),
        });

        // 存入 KV，10 分钟后自动过期
        await env.EMAILS.put(recipient, data, { expirationTtl: 600 });
      }
    } catch (e) {
      // Worker 中的错误不应影响邮件投递
      console.error('Email Worker error:', e);
    }
  },
};
