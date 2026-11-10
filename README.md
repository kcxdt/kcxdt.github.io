# KCDT 美股趨勢溫度計

每個交易日美股收市後，GitHub Actions 會：

1. 由 Wikipedia 拎 S&P 500 成份股名單
2. 由 Yahoo Finance 下載兩年日線（已調整拆股及派息）
3. 計市場溫度（8 個指標）、市場寬度、板塊強弱、強勢股
4. 生成 `site/index.html`，發佈到 GitHub Pages

更新時間（全部 GitHub Actions，時間係 UTC）：

- `daily.yml` 收市更新：21:50，後備 22:50／23:50／01:50／03:50（已經有當日收市就幾秒內停）。另外有 Claude 排程喺紐約 17:47 檢查，未有就手動觸發。只做價格同少量基本面，約 10 分鐘。
- `warm.yml` 中午基本面更新：17:40，後備 19:40。逐隻拎營收、EPS、行業、下次業績日，唔發佈，只存 cache 俾下次收市更新用。
- `premarket.yml` 盤前需知、`econ.yml` 經濟數據同新聞。

本地試行：

```
pip install -r requirements.txt
python -m kcdt          # 生成 site/index.html
python -m tests.test_build   # 唔使上網嘅測試
```

計法同門檻喺 `kcdt/compute.py`。只供數據及教育用途，唔構成投資建議。
