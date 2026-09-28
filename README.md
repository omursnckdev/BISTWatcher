# BISTWatcher

Borsa İstanbul için günlük, açıklanabilir teknik analiz tarayıcısı. **Faz 1**
uygulamasıdır; hedef araştırma ufku 3–20 işlem günüdür. Emir göndermez.

## Kurulum ve çalıştırma

Python 3.12 veya üzeri:

```bash
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .
bistwatcher --demo --as-of 2026-09-25
bistwatcher --demo --as-of 2026-09-25 --json --output reports/demo.json
```

Demo verileri deterministik ve tamamen sentetiktir; gerçek fiyat veya güncel
piyasa önerisi değildir. Demo takvimi yalnızca hafta içidir; BIST tatillerini içermez.

## Kendi verinle tarama

Her sembol ve endeks için `data/THYAO.csv`, `data/XU100.csv` gibi dosyalar sağlayın:

```csv
date,open,high,low,close,volume
2025-01-02,100,103,99,102,1500000
```

Bu satır yalnızca şema örneğidir. En az 250 günlük bar gerekir. Sembol listesini
`config/settings.toml` dosyasından düzenleyin; verilen beş sembol örnek seçimdir,
güncel BIST30 üyelik listesi değildir.

```bash
bistwatcher --data-dir data --config config/settings.toml --as-of 2026-09-25 --output reports/scan.json
```

`--as-of` tamamlanmış son seansı belirtmelidir. Verinin gün sonu kesinleştiğini
sağlayıcı doğrulamalıdır. Kesim tarihinden sonraki barlar kullanılmaz. Eksik
seanslar doldurulmaz; son 250 hisse ve endeks seansı birebir uyuşmalıdır.
Varsayılan eskime sınırı beş takvim günüdür; resmî tatil takvimi entegrasyonu yoktur.

OHLC aynı fiyat düzeltme bazında, hacim ise o bazla tutarlı olmalıdır. Bölünme ve
bedelsiz işlemlerinin düzeltmesi sağlayıcının sorumluluğudur; `adjusted_close`
kolonu tek başına otomatik düzeltme yapmaz. Temettü/toplam getiri analizi henüz yoktur.

## Mevcut kapsam

- EMA20/50/100/200, Wilder RSI14/ATR14/ADX14, MACD12/26/9, Bollinger20/2.
- Önceki 20 seans hacmine göre hacim oranı, 5/20/60 seans endekse göre getiri.
- XU100 için BULL / NEUTRAL / BEAR / HIGH_VOLATILITY sınıflaması.
- Faktör dökümü, pozitif/negatif koşullar ve işlem engelleri.
- ATR stop, 1.5R/2.5R hedefleri, tam adet pozisyon boyutu ve pozisyon limiti.
- CSV sağlayıcısı, sentetik demo, TOML ayarları ve JSON raporu.

Teknik faktörler şartnamedeki 100 puanın **70** puanını kapsar. Ekrandaki skor
`ham teknik puan / 70 * 100` olarak normalize edilir. Haber/KAP ve kurum akışı
**mevcut değil** olarak raporlanır; eksik veriye nötr veya pozitif puan verilmez.
Bu skor tam birleşik 100 puanlık modelle doğrudan karşılaştırılmamalıdır.

Alım adayı eşiği boğada 75, nötrde 80; güçlü aday eşiği 85'tir. Ayı ve yüksek
oynaklık rejimleri long adayları engeller. ATR/fiyat > %4 yüksek oynaklık için
başlangıç kuralıdır. Likidite ve risk filtreleri tüm skorlardan önce uygulanır.
Eşikler araştırma varsayımlarıdır; backtest ile henüz doğrulanmamıştır.

Giriş fiyatı T kapanışından bir **referanstır**; T+1 açılışında gerçekleşme
varsayılmaz. Risk ve miktar gerçek yürütülebilir fiyatla yeniden hesaplanmalıdır.
Risk/getiri hedef katsayısıdır, başarı olasılığı değildir. Portföyde mevcut
pozisyonlar, komisyon, kayma ve gap riski bu ilk aşama hesaplarına dahil değildir.

## Testler

```bash
python -m unittest discover -s tests -v
```

Kurulum yapmadan çalışma: `PYTHONPATH=src python -m bist_quant --demo`.
CLI çıkış kodları: 0 başarılı; 1 sembol bazında kısmi hata; 2 ayar/veri hatası.
JSON raporundaki `errors` alanını daima kontrol edin.

## Sonraki aşamalar

1. Lisanslı piyasa veri adaptörü, kurumsal aksiyonlar ve seans takvimi.
2. Veritabanı; T kapanışı → T+1 açılışı, maliyetler ve portföy riskli backtest.
3. Haber/KAP, kurum akışı ve veri kapsamına göre birleşik model.
4. API/dashboard; ileri yürüyen doğrulama ve paper trading.

Ana tasarım: [Proje şartnamesi](BIST_Quant_Trading_Bot_Project_Spec.md).
