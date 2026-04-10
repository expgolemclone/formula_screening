# CLAUDE.md

## プロキシ失敗キャッシュ

- プロキシ失敗キャッシュ（`data/.proxy_failures.json`）を安易に全クリアしないこと
- キャッシュはTTLベースで自動失効する（`tcp_unreachable`=1h, `not_a_proxy`/`https_tunnel_failed`=24h）
- 全クリアすると21万件超の不良プロキシを毎回再チェックすることになり、20分以上の無駄が生じる
- キャッシュクリアが必要な場合は、特定の理由（reason）のみを対象にすること
