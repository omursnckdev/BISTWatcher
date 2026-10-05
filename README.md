# BISTWatcher

Borsa İstanbul hisselerini her gün tarayan ve **3 ila 20 günlük** al-sat fırsatlarını
arayan bir araçtır. Her hisseye 0 ile 100 arasında bir **puan** verir, puanı neden
verdiğini tek tek yazar ve her aday için **giriş, stop, hedef ve lot sayısı** önerir.

- Yalnızca sinyal üretir. **Emir göndermez**, hesabınıza bağlanmaz.
- Çıkan sinyaller yatırım tavsiyesi değildir. Eşikler henüz başlangıç tahminleridir.
- Ayrıntılı İngilizce teknik belge: [docs/technical-reference.md](docs/technical-reference.md)

## 1. Kurulum

Python 3.11 veya üstü gerekir (3.12 önerilir).

```bash
git clone https://github.com/omursnckdev/BISTWatcher.git
cd BISTWatcher
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

API anahtarı gerekmez. Fiyatlar Yahoo Finance'ten, şirket haberleri KAP'tan ücretsiz çekilir.

## 2. İlk tarama

```bash
python -m bist_quant tara
```

Bu komut BIST 100 hisselerini tarar, piyasa durumunu belirler ve hisseleri puana göre
sıralar. İnternet yoksa ya da sadece denemek istiyorsanız sahte verilerle çalıştırın:

```bash
python -m bist_quant tara --kaynak synthetic
```

## 3. Komutlar

Her komutun ve seçeneğin İngilizce adı da çalışır (`tara` yerine `scan` gibi).

| Komut | Ne yapar |
|---|---|
| `tara` | Hisseleri puanlar ve sıralı tablo yazar |
| `analiz THYAO` | Tek hissenin tüm gerekçelerini ve risk planını gösterir |
| `piyasa` | Sadece piyasanın genel durumunu (BOĞA, AYI vb.) gösterir |
| `evren` | Taranan hisse listesini yazar |
| `haber THYAO` | Hissenin son KAP bildirimlerini ve haber puanını gösterir |
| `kap-indir` | KAP bildirim geçmişini indirir |
| `geritest` | Stratejiyi geçmiş verilerde, komisyon dahil dener |
| `parametre-tara` | Farklı ayarları aynı dönemde karşılaştırır |
| `ileri-test` | Ayarı geçmişte seçip sonraki, görülmemiş yılda test eder |
| `arastirma` | Puanın gelecek getiriyi ne kadar tahmin ettiğini ölçer |

`tara` için sık kullanılan seçenekler:

| Seçenek | Örnek | Ne yapar |
|---|---|---|
| `--evren` | `--evren BIST30` | BIST30, BIST50, BIST100 veya CUSTOM listesini tarar |
| `--semboller` | `--semboller THYAO ASELS` | Sadece verilen hisseleri tarar |
| `--ilk` | `--ilk 10` | Tablonun sadece ilk 10 satırını gösterir |
| `--detay` | `--detay` | Her hisse için ayrıntılı döküm yazar |
| `--en-az-sinyal` | `--en-az-sinyal WEAK_SETUP` | Daha zayıf sinyalleri gizler |
| `--alim-esigi` | `--alim-esigi 60` | AL eşiğini bu tarama için değiştirir (bkz. bölüm 5) |
| `--tarih` | `--tarih 2025-06-30` | Geçmişteki bir günü, o günün verisiyle tarar |
| `--habersiz` | `--habersiz` | KAP haberlerini hesaba katmaz |
| `--kaynak` | `--kaynak csv` | Veri kaynağı: `yahoo`, `csv` veya `synthetic` |
| `--yenile` | `--yenile` | Önbelleği atlayıp veriyi yeniden indirir |
| `--json` | `--json sonuc.json` | Sonucu JSON dosyasına yazar (`-` ekrana yazar) |
| `--kaydet` | `--kaydet` | Sinyalleri ve göstergeleri `data/processed/` altına CSV yazar |

Tüm seçenekler için: `python -m bist_quant tara --help`

## 4. Tablo nasıl okunur

```text
Market regime (XU100): BEAR  [regime score 0.0/10, breadth 13% above EMA50]  -> BUY threshold 80

  #  SYMBOL  SCORE  SIGNAL           CLOSE   R:R   TRND   MOM   VOL    RS   REG  VLTY
  1  FENER    64.6  WEAK_SETUP      806.55   2.0   12.5  13.4   5.0   9.3   0.0   5.0
```

| Sütun | Anlamı |
|---|---|
| `Market regime` | Piyasanın genel durumu ve o gün geçerli AL eşiği |
| `SCORE` | 0-100 arası toplam puan |
| `SIGNAL` | Puan ve risk kontrollerine göre sonuç (aşağıdaki tablo) |
| `CLOSE` | Son kapanış fiyatı |
| `R:R` | Getiri/risk oranı. Hedefe kadar olası kazanç, stopa kadar olası kaybın kaç katı |
| `TRND` | Trend puanı (en fazla 20) |
| `MOM` | Momentum puanı (en fazla 15) |
| `VOL` | Hacim puanı (en fazla 10) |
| `RS` | XU100'e göre göreceli güç (en fazla 10) |
| `REG` | Piyasa durumu puanı (en fazla 10) |
| `VLTY` | Oynaklık ve kurulum kalitesi (en fazla 5) |
| `NEWS` | KAP haber puanı (en fazla 15, haber yoksa nötr 7.5) |

Sinyaller:

| Puan | Sinyal | Anlamı |
|---|---|---|
| 45'ten az | `NO_TRADE` | İşlem yok |
| 45 - 54 | `WATCH` | İzlemeye değer |
| 55 - AL eşiği | `WEAK_SETUP` | Kurulum var ama yeterince güçlü değil |
| AL eşiği ve üstü | `BUY_CANDIDATE` | Alım adayı |
| AL eşiği + 10 ve üstü | `STRONG_BUY_CANDIDATE` | Güçlü alım adayı |

Puan tek başına yetmez. AL adayı olmak için ayrıca şunlar gerekir:

- **Getiri/risk en az 2 olmalı.** Hisse yakın bir dirence çok yakınsa, hedefe kadar kazanç
  payı küçülür ve sinyal `WEAK_SETUP` olarak kalır.
- **Hisse yeterince likit olmalı.** 20 günlük ortalama işlem hacmi 50 milyon TL'nin
  altındaysa sinyal `NO_TRADE` olur.
- **Veri güncel olmalı.** İşlem görmeyen ya da verisi eski hisseler atlanır.

## 5. AL eşikleri ve gevşetme

AL eşiği piyasanın durumuna göre değişir. Piyasa kötüyse daha yüksek puan istenir.

| Piyasa durumu | Ne zaman | AL eşiği | Güçlü AL |
|---|---|---:|---:|
| `BULL` (boğa) | XU100 yükseliş trendinde | 65 | 75 |
| `NEUTRAL` (nötr) | Belirgin bir yön yok | 70 | 80 |
| `HIGH_VOLATILITY` (yüksek oynaklık) | Endeks sert dalgalanıyor ya da 60 günlük zirvesinden %15+ düşmüş | 75 | 85 |
| `BEAR` (ayı) | XU100 düşüş trendinde | 80 | 90 |

Bu eşikler Ekim 2026'da düşürüldü. Eski değerler 75 / 80 / 85 / 88 idi, `WATCH` 50'de,
`WEAK_SETUP` 65'te başlıyordu.

Daha fazla aday görmek için:

- **Tek bir taramada:** `python -m bist_quant tara --alim-esigi 60` yazın. Bu, BOĞA eşiğini
  60 yapar ve diğer durumları da aynı miktarda (5 puan) indirir.
- **Kalıcı olarak:** `config/settings.yaml` içindeki `strategy.buy_threshold` değerlerini
  değiştirin.
- **Direnç kontrolünü kapatmak için:** `config/settings.yaml` içinde
  `risk.use_resistance_cap: false` yapın. Bu, direnç yüzünden düşük görünen getiri/risk
  oranını yok sayar. Riski artırır, dikkatli kullanın.

Eşikleri değiştirmeden önce `geritest` ile geçmişte nasıl sonuç verdiğine bakmanız önerilir:

```bash
python -m bist_quant parametre-tara --izgara "strategy.buy_threshold_offset=[-10,-5,0,5]"
```

## 6. Puan nasıl hesaplanır

Puan sekiz parçadan oluşur. Her parçanın en fazla alabileceği puan:

| Parça | Puan | Neye bakar |
|---|---:|---|
| Trend | 20 | Fiyatın EMA20/50/200 üstünde olması, ortalamaların sırası, ADX trend gücü |
| Momentum | 15 | RSI, MACD, son 10 günün değişimi |
| Hacim | 10 | Yükselen günlerde hacim artışı, OBV, birikim/dağıtım |
| Göreceli güç | 10 | Hissenin son 5, 20 ve 60 günde XU100'den ne kadar iyi gittiği |
| Haber / KAP | 15 | KAP bildirimlerinin yönü, önemi, büyüklüğü ve fiyatın tepkisi |
| Kurumsal akış | 15 | Henüz yok (Faz 4) |
| Piyasa durumu | 10 | XU100'ün trendi ve hisselerin kaçının EMA50 üstünde olduğu |
| Oynaklık | 5 | ATR'nin makul aralıkta olması, fiyatın aşırı uzamamış olması |

Kurumsal akış henüz olmadığı için toplam 85 puan üzerinden hesaplanıp 100'e ölçeklenir.
Haberler kapalıysa (`--habersiz`) 70 üzerinden hesaplanır.

## 7. Risk planı

Her aday için şu öneriler verilir:

- **Giriş:** Sinyal kapanışta oluşur, alım ertesi gün kapanış ± 0.25 ATR aralığında varsayılır.
- **Stop:** Giriş - 2 × ATR(14).
- **Hedef 1:** Giriş + 1.5 × risk. Burada pozisyonun yarısı satılır, stop maliyete çekilir.
- **Hedef 2:** Giriş + 2.5 × risk.
- **Lot:** Stop olursa portföyün %1'i kaybedilecek şekilde hesaplanır (varsayılan portföy
  500.000 TL). Tek hisseye portföyün en fazla %20'si konur.

## 8. Ayar dosyaları

| Dosya | İçerik |
|---|---|
| `config/settings.yaml` | Veri kaynağı, gösterge ayarları, AL eşikleri, risk, likidite, haber ayarları |
| `config/scoring.yaml` | Parçaların ağırlıkları ve puanlama kuralları |
| `config/universe.yaml` | Hisse listeleri (BIST30, BIST50, BIST100, CUSTOM) |
| `config/backtest.yaml` | Geri test dönemi, komisyon, çıkış kuralları |

Hatalı bir değer girerseniz program açılırken ne olduğunu söyleyip durur.

## 9. Kendi verinizle çalışma

`data/raw/` klasörüne her hisse için `THYAO.csv` gibi bir dosya koyun:

```csv
date,open,high,low,close,volume
2025-01-02,100,103,99,102,1500000
```

Sonra `python -m bist_quant tara --kaynak csv` ile tarayın. XU100 için de dosya gerekir.

## 10. Bilinmesi gerekenler

- **Hisse listeleri elle tutulur.** BIST endeks üyelikleri her çeyrek değişir;
  `config/universe.yaml` dosyasını güncel tutun.
- **Yahoo ve KAP verileri resmi değildir.** Eksik ya da gecikmeli veri olabilir. KAP'a
  ulaşılamazsa tarama haber puanı olmadan devam eder.
- **Geleceğe bakma yoktur.** Her hesap sadece o güne kadarki veriyi kullanır. Seans açıkken
  (18:15'ten önce) günün yarım mumu kullanılmaz.
- **Geri testte hayatta kalma yanlılığı vardır.** Geçmiş taramalar bugünkü hisse listesini
  kullanır.

## 11. Testler

```bash
pytest                                       # internetsiz tüm testler
BIST_QUANT_NETWORK_TESTS=1 pytest -k live    # isteğe bağlı canlı Yahoo testi
```

## 12. Yol haritası

| Faz | Kapsam | Durum |
|---|---|---|
| 1 | Teknik tarayıcı, piyasa durumu, puanlama, risk planı | Tamam |
| 2 | Geri test, ileri test, faktör araştırması | Tamam |
| 3 | KAP bildirimleri ve haber puanı | Tamam |
| 4 | Kurumsal / aracı kurum akışı (BofA dahil), takas verisi | Sırada |
| 5 | Birleşik model ve doğrulama | Planlandı |
| 6-8 | Panel, kağıt üzerinde işlem, isteğe bağlı gerçek emir | Planlandı |
