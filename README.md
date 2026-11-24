# bladeRF Workbench Mobile

אפליקציית ספקטרום אנלייזר לאנדרואיד שעובדת עם bladeRF 2.0 micro. מחברים את הכרטיס לטלפון בכבל USB‑C OTG ורואים את הספקטרום.

מנוע הקליטה הוא הקוד של תוכנת המחשב `bladeRF Workbench.py`, בלי שינוי. זה כולל את שכבת libbladeRF, הסריקה והתפירה של Span רחב, תכנון ה-RBW, הגלאים, ה-Max Hold והמיצוע, טבלת הכיול, מזהה הקריירים ותוכנית הפסים. בטלפון הקוד הזה רץ ב-Python (Chaquopy), והממשק נבנה מחדש למסך מגע.

## מה יש באפליקציה

- **ספקטרום חי** ב-dBm על גרטיקולה של ‎10×10. יש Ref level, ‏dB/div, ‏RBW אוטומטי או ידני, Detector, הגבר RX ו-Cal offset.
- **ארבע עקומות:** Live, ‏Max Hold, ‏Average ו-Min Hold, ו-Restart שמאפס אותן.
- **מעבר בין תדרים:** גרירה בגרף מזיזה את התדר, צביטה עושה זום, וגרירה על סקלת ה-dB מזיזה את ה-Ref level. אפשר גם להקליד Center/Span/Start/Stop (‏`2.44G`, ‏`915M`, ‏`433.92 MHz`). יש כפתורי Span מהירים וצעד מרכז.
- **מרקרים (6):** נגיעה בגרף מציבה את המרקר, ונגיעה כפולה מעבירה אותו לשיא. יש Next peak (גם שמאלה וימינה), מרקר דלתא ביחס ל-M1, ‏Mkr→Center, ‏Mkr→Ref ומעקב שיא. ליד המרקר מופיע שם הפס (B3, ‏B20, ‏ISM 2.4 וכו').
- **זיהוי אות:** לחיצה ארוכה על אות מראה את סוג הטכנולוגיה (LTE/NR/WiFi/GSM…), את הרוחב התפוס ואת הפס. זה המזהה של תוכנת המחשב.
- **רשימת אותות** בתחום שעל המסך: התדר, הרמה, הרוחב, הטכנולוגיה והפס. נגיעה בשורה עושה זום לאות. "סקירת אזור" סורקת את כל התחום, 70 MHz עד 6 GHz.
- **Waterfall** מתחת לספקטרום, ופס של תוכנית התדרים מעל הגרף.
- **פריסטים של תחומי תדר:** שורת כפתורים מעל הגרף. אפשר להוסיף פריסט, לערוך, למחוק ולשנות את הסדר. אפשר לשמור את התצוגה הנוכחית כפריסט, וגם לייצא ולייבא קובץ פריסטים. לכל פריסט יש שם באנגלית ושם בעברית, ואפשר לשמור בו גם RBW והגבר.
- **עברית ואנגלית:** כפתור בשורה העליונה, והממשק עובר ל-RTL.
- **סיבוב מסך:** הכפתור ⟳ עובר בין אוטומטי, נעול לאורך ונעול לרוחב. במצב לרוחב המקשים בצד, כמו בתוכנת המחשב, ובמצב לאורך הם למטה.
- **שמירה:** צילום מסך PNG, ועקומה כ-CSV + Excel (בכותב ה-xlsx של תוכנת המחשב). אפשר לשתף כל קובץ ממסך השמירה.
- **לשונית מפה (סקר לפי מיקום):** כל מדידה נשמרת עם מיקום ה-GPS של הטלפון, כולל JPEG של הספקטרום, שם אופציונלי ובחירת סקר יעד. "שמירה + העתק אל…" שומר עותק לכל יעד בטלפון. נגיעה בנקודה במפה מציגה את הניתוח שנשמר.
  - **מסלול בזמן אמת:** נקודה כל כמה שניות, עם ה-Max Hold וההספק הממוצע מאז הנקודה הקודמת. הנקודות נצבעות לפי העוצמה (כחול = חלש, אדום = חזק).
  - **תמונת אזור:** מעלים צילום לוויין או שרטוט ומכיילים אותו ב-2 או 3 נקודות. הנקודות יושבות על התמונה לפי ה-GPS. יש גם מפת רחובות (OSM, אונליין) או רשת.
  - **ייצוא:** KML (Google Earth), CSV, ומפה כ-JPEG.
  - **מיקום:** אין צורך באנטנת GPS. המיקום מגיע מהטלפון, ובתוך מבנה אפשר למקם נקודה ידנית.
- **סימולטור נפרד:** אייקון שני, "bladeRF Simulator", שנפתח ישר על סצנה מדומה. הצ'יפ העליון מראה תמיד את מקור הנתונים: ירוק (bladeRF מחובר), כתום (סימולטור) או אדום (מנותק). נקודות מהסימולטור או מ-GPS מדומה מסומנות "נתונים מדומים".
- **אזהרות:** INPUT OVERLOAD, רמה מעל ‎−10 dBm בכניסה וניתוק של הכרטיס. אחרי ניתוק החיבור חוזר לבד.
- **הגדרות:** נשמרות לפעם הבאה.

המחולל (TX) של תוכנת המחשב לא נכלל. האפליקציה הזאת היא לקליטה ולצפייה בלבד.

## בנייה ב-Android Studio

1. **Python 3.11 על המחשב.** Chaquopy משתמש בו כדי להוריד numpy לאנדרואיד. התקינו מ-python.org ‏(Windows installer 64‑bit, גרסה 3.11.x).
2. ב-Android Studio: ‏**File → Open** ובוחרים את התיקייה **`android`**.
3. מחכים ל-Gradle Sync. הוא מוריד לבד את Gradle, ‏AGP, ‏Chaquopy, ‏NDK ו-CMake (אם מופיעה בקשה לאשר רישיונות SDK, מאשרים). אם ה-NDK או ה-CMake לא הותקנו: **SDK Manager → SDK Tools →** ‏NDK (Side by side) ו-CMake 3.22.1.
4. בונים את ה-APK, באחת משתי הדרכים:
   - **Build → Build App Bundle(s) / APK(s) → Build APK(s).** הקובץ נוצר ב-`android/app/build/outputs/apk/debug/app-debug.apk`.
   - מחברים טלפון עם USB debugging ולוחצים **Run ▶**.

אם Gradle כותב שהוא לא מוצא את Python 3.11, פותחים את `android/app/build.gradle.kts` ומוסיפים בתוך `chaquopy { defaultConfig { … } }` את השורה:
`buildPython("C:/Users/<you>/AppData/Local/Programs/Python/Python311/python.exe")`

## חיבור ה-bladeRF לטלפון

- מחברים בכבל USB‑C OTG. הכרטיס צורך הרבה זרם. אם הוא לא עולה, או מתנתק בזמן סריקה, צריך USB hub עם ספק כוח (או הזנת DC לכרטיס).
- בחיבור הראשון Android שואל אם לפתוח את האפליקציה ולתת לה גישה. כדאי לסמן "תמיד".
- **קובץ FPGA:** אם הכרטיס לא טוען FPGA מהזיכרון שלו, מעתיקים את `hostedxA4.rbf` מהמחשב (מתיקיית ההתקנה של bladeRF) לטלפון, ובוחרים אותו פעם אחת ב-**רדיו → טען hostedxA4.rbf**. אפשר גם לשים אותו ב-`android/app/src/main/assets/fpga/` לפני הבנייה, ואז הוא נכנס ל-APK.
- **USB 2 או USB 3:** רוב הטלפונים מחברים ב-USB 2. במצב אוטומטי האפליקציה מגבילה ל-10 MS/s ב-USB 2 ומאפשרת 40 MS/s ב-USB 3. אפשר לשנות את זה ב-**RBW → קצב דגימה מרבי**.
- כשאין כרטיס מחובר, האפליקציה עובדת על הסימולטור של תוכנת המחשב.

## תצוגה מקדימה על המחשב, בלי טלפון

```
py -3 -m pip install numpy
py -3 android\app\src\main\python\mobile_app.py --sim
```
פותחים את http://127.0.0.1:8765 ב-Chrome, ‏F12, ומפעילים את מצב הטלפון (Ctrl+Shift+M).

## בדיקות

```
py -3 tests\test_backend.py                  47 checks: engine, API, presets, surveys, walks, exports
node tests\test_ui.js                        every panel, both orientations, he/en (needs Playwright)
node tests\test_map.js                       map: save point, walk, upload + calibrate image, open points
```

## מבנה התיקייה

```
android/                         Android Studio project (Kotlin + Chaquopy)
  app/src/main/python/
    workbench_core.py            = "bladeRF Workbench.py", UNCHANGED (engine)
    mobile_app.py                phone back end: controller, presets, signal list, HTTP on 127.0.0.1
    survey.py                    location surveys: points, walks, area image + calibration, KML/CSV
    PyQt5/, pyqtgraph/, _vqt_core.py   headless stand-ins so the desktop file imports as-is
  app/src/main/assets/webui/     the touch UI (HTML/CSS/JS, Hebrew + English); map.js = Map tab
  app/src/main/java/.../MainActivity.java USB permission + fd, location, rotation, sharing, file pickers
native/                          libbladeRF 2.6.1 + libusb 1.0.29 sources, built by the NDK
  libbladeRF-android-fd.patch    the ONE change to libbladeRF: open the board via Android's USB fd
tests/
docs/User Guide - Cellular Measurements (HE).pdf   user guide (Hebrew)
```

**עדכון המנוע:** כשיוצאת גרסה חדשה של `bladeRF Workbench.py`, מעתיקים אותה על `android/app/src/main/python/workbench_core.py`, מריצים את `tests\test_backend.py` ובונים מחדש.

## מה נבדק ומה עוד לא

- **נבדק:** המנוע רץ על Python 3.11 עם numpy 1.26, כמו בטלפון, ועובר את כל 47 הבדיקות. בדיקת המפה עוברת מקצה לקצה: שמירת נקודה, מסלול, העלאת תמונה, כיול ופתיחת נקודות. הממשק נבדק בדפדפן בגודל של טלפון, לאורך ולרוחב, בעברית ובאנגלית, בלי שגיאות JavaScript. ‏libbladeRF עם התיקון נבנה ב-clang. נתיב ה-USB fd נבדק עד כשל נקי כשאין כרטיס. ‏MainActivity.java עבר הידור ב-javac.
- **עוד לא נבדק:** בנייה מלאה ב-Gradle (אין כאן גישה להורדות של Google), bladeRF אמיתי שמחובר לטלפון, ו-GPS אמיתי בטלפון. בדפדפן ה-GPS נבדק דרך מיקום מדומה.
