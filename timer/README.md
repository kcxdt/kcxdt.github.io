# KCDT 外部鬧鐘（Cloudflare Worker）

GitHub 自己嘅排程成日遲幾個鐘甚至跳過。呢個 Worker 喺 Cloudflare 免費計劃度準時叫 GitHub 開跑盤前需知同收市更新。
佢只係「叫開跑」，唔會攞價、唔會改網站。如果當日已經更新咗、係假期或者未到時間，GitHub 嗰邊幾秒內自己停，所以叫多幾次都冇問題。

## 設定（一次過，約 10 分鐘）

### 1. 整一條 GitHub 鎖匙（token）

1. 登入 GitHub，去 <https://github.com/settings/personal-access-tokens/new>
2. Token name：`kcdt-timer`；Expiration：揀最長（一年），記低到期日，到期要換新。
3. Resource owner：`kcxdt`
4. Repository access：Only select repositories → `kcxdt.github.io`
5. Permissions → Repository permissions → **Actions：Read and write**（其他唔好揀）
6. Generate token，複製 `github_pat_...` 嗰串字。只會顯示一次，唔好貼去任何聊天或者檔案，直接貼去下面第 3 步。

### 2. 開 Cloudflare 帳戶同 Worker

1. 去 <https://dash.cloudflare.com/sign-up> 開免費帳戶（唔使信用卡）。
2. 左邊 Workers & Pages → Create → 揀「Hello World」Worker → 名改做 `kcdt-timer` → Deploy。
3. 撳 Edit code，刪晒入面嘅字，貼入 `timer/worker.js` 全部內容 → Deploy。

### 3. 放鎖匙

Worker 頁 → Settings → Variables and Secrets → Add → Type 揀 **Secret**，Name 填 `GH_TOKEN`，Value 貼第 1 步條鎖匙 → Deploy。

### 4. 加鬧鐘時間

Worker 頁 → Settings → Trigger Events → Add → Cron Triggers，加三條（時間係 UTC）：

```
5 10,11,13 * * MON-FRI
40 20,21,23 * * MON-FRI
35 17,19 * * MON-FRI
```

第一條係盤前（紐約 06:05，夏令冬令各一個，再加一次後備），第二條係收市（紐約 16:40，同樣）。第三條係中午預先更新基本面（`warm.yml`，第二次係後備）。

## 點知有冇用

- GitHub → Actions，會見到由你個帳戶 `workflow_dispatch` 觸發嘅 run，時間貼近上面嘅鐘數。
- Cloudflare Worker 頁 → Logs / Observability 可以睇每次有冇成功；見到 `401` 或 `403` 即係鎖匙過期或者權限唔啱。
