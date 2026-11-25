package com.netline.bladerfmobile;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ActivityInfo;
import android.content.pm.PackageManager;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import androidx.activity.ComponentActivity;
import androidx.activity.OnBackPressedCallback;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.core.content.ContextCompat;
import androidx.core.content.FileProvider;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.TimeZone;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * bladeRF Workbench Mobile.
 *
 * Three pieces:
 *  - the desktop program's engine (Python, run by Chaquopy) serving the UI and
 *    the spectrum on 127.0.0.1,
 *  - a WebView showing that UI,
 *  - this Activity, which owns what only Android can do: USB permission and
 *    the device descriptor, location, screen rotation, sharing and picking files.
 *
 * Written in Java (not Kotlin) on purpose: the project then needs no Kotlin
 * Gradle plugin, which is one download fewer that a network can block.
 *
 * USB: an unrooted app cannot open /dev/bus/usb itself. UsbManager opens the
 * board after the user allows it and gives us a file descriptor; the patched
 * libbladeRF wraps that descriptor (bladerf_set_usb_fd). The connection object
 * must stay referenced for as long as the descriptor is in use.
 */
public class MainActivity extends ComponentActivity {

    private static final String TAG = "bladeRFMobile";
    private static final int REQ_LOCATION = 41;
    private static final String ACTION_USB_PERMISSION = "com.netline.bladerfmobile.USB_PERMISSION";
    private static final String LOADING_HTML =
        "<body style='background:#07090b;color:#8fa6bb;font:16px sans-serif;display:flex;"
        + "align-items:center;justify-content:center;height:90vh;flex-direction:column'>"
        + "<div style='color:#ffd24a;font-weight:bold;font-size:20px'>YT Spectrum Analyzer</div>"
        + "<div style='margin-top:12px'>Starting the engine… / מפעיל את המנוע…</div></body>";

    private WebView web;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private volatile PyObject engine;
    private int port = 0;
    private UsbDeviceConnection usbConn;
    private UsbDevice usbDev;
    private UsbManager usb;
    private LocationManager loc;
    private boolean askedLocation = false;
    private File appData;
    private boolean locOn = false;
    private Location lastFix;
    private ValueCallback<Uri[]> fileCallback;
    private File saveAsSource;
    private String pickKind = "";

    // Opened from the "bladeRF Simulator" icon: start on the simulated scene.
    private static boolean simLaunch(Intent i) {
        return i != null && i.getComponent() != null
            && i.getComponent().getClassName().endsWith("SimulatorAlias");
    }

    // ------------------------------------------------------------ lifecycle

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        usb = (UsbManager) getSystemService(Context.USB_SERVICE);
        loc = (LocationManager) getSystemService(Context.LOCATION_SERVICE);
        File ext = getExternalFilesDir(null);
        appData = ext != null ? ext : getFilesDir();
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        web = new WebView(this);
        web.setBackgroundColor(0xFF07090B);
        web.getSettings().setJavaScriptEnabled(true);
        web.getSettings().setDomStorageEnabled(true);
        web.getSettings().setMediaPlaybackRequiresUserGesture(false);
        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient() {
            // <input type=file> in the page (uploading an area image)
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> cb,
                                             FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = cb;
                pickKind = "upload";
                List<String> types = new ArrayList<>();
                if (params != null && params.getAcceptTypes() != null)
                    for (String t : params.getAcceptTypes()) if (t != null && !t.trim().isEmpty()) types.add(t);
                picker.launch(types.isEmpty() ? new String[]{"*/*"} : types.toArray(new String[0]));
                return true;
            }
        });
        web.addJavascriptInterface(new Bridge(), "Android");
        setContentView(web);
        web.loadDataWithBaseURL(null, LOADING_HTML, "text/html", "utf-8", null);

        getOnBackPressedDispatcher().addCallback(this, new OnBackPressedCallback(true) {
            @Override
            public void handleOnBackPressed() {
                web.evaluateJavascript("window.onBack ? window.onBack() : false", r -> {
                    if (!"true".equals(r)) finish();
                });
            }
        });

        IntentFilter filter = new IntentFilter();
        filter.addAction(ACTION_USB_PERMISSION);
        filter.addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED);
        filter.addAction(UsbManager.ACTION_USB_DEVICE_DETACHED);
        ContextCompat.registerReceiver(this, usbReceiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED);

        worker.execute(this::startEngine);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        if (simLaunch(intent)) {
            worker.execute(() -> call("use_simulator"));
            return;
        }
        // Launched (again) by plugging the board in.
        if (UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(intent.getAction())) {
            UsbDevice dev = deviceFrom(intent);
            if (dev == null) dev = findBladeRF();
            if (dev != null) connectUsb(dev);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        // Back from Settings with the permission granted or GPS switched on:
        // re-register so the new provider is actually used.
        if (locOn && loc != null
            && checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
            stopLocation();
            startLocation();
        }
    }

    @Override
    protected void onDestroy() {
        stopLocation();
        try { unregisterReceiver(usbReceiver); } catch (Exception ignored) { }
        worker.execute(() -> {
            call("stop_server");
            if (usbConn != null) usbConn.close();
            usbConn = null;
        });
        worker.shutdown();
        super.onDestroy();
    }

    /** engine.callAttr that never throws (logs instead). */
    private PyObject call(String fn, Object... args) {
        PyObject e = engine;
        if (e == null) return null;
        try {
            return e.callAttr(fn, args);
        } catch (Exception ex) {
            Log.w(TAG, fn, ex);
            return null;
        }
    }

    // --------------------------------------------------------------- engine

    private void startEngine() {
        try {
            File webDir = new File(getFilesDir(), "webui");
            copyAssetDir("webui", webDir);
            // An FPGA image shipped in the APK (assets/fpga/hostedxA4.rbf) is
            // handed to libbladeRF; a picked one lands in the same folder.
            File fpgaDir = new File(appData, "fpga");
            fpgaDir.mkdirs();
            try {
                String[] names = getAssets().list("fpga");
                if (names != null) for (String n : names) {
                    File out = new File(fpgaDir, n);
                    if (!out.exists()) copy(getAssets().open("fpga/" + n), new FileOutputStream(out));
                }
            } catch (Exception ignored) { }

            if (!Python.isStarted()) Python.start(new AndroidPlatform(this));
            PyObject m = Python.getInstance().getModule("mobile_app");
            engine = m;

            boolean sim = simLaunch(getIntent());
            UsbDevice dev = findBladeRF();
            int fd = -1;
            if (dev != null && usb.hasPermission(dev) && !sim) fd = openUsb(dev);
            String lib = new File(getApplicationInfo().nativeLibraryDir, "libbladeRF.so").getAbsolutePath();
            port = m.callAttr("start_server", appData.getAbsolutePath(), webDir.getAbsolutePath(),
                0, lib, sim, fd, TimeZone.getDefault().getID()).toInt();
            runOnUiThread(() -> web.loadUrl("http://127.0.0.1:" + port + "/"));
            final UsbDevice d = dev;
            if (dev != null && fd < 0 && !sim) runOnUiThread(() -> askPermission(d));
        } catch (Throwable e) {
            Log.e(TAG, "engine start failed", e);
            String msg = String.valueOf(e.getMessage() != null ? e.getMessage() : e.toString()).replace("<", "&lt;");
            runOnUiThread(() -> web.loadDataWithBaseURL(null,
                "<body style='background:#12161a;color:#ff8a80;font:14px sans-serif;padding:20px;white-space:pre-wrap'>"
                    + "Engine failed to start:\n\n" + msg + "</body>", "text/html", "utf-8", null));
        }
    }

    private static void copy(InputStream in, OutputStream out) throws Exception {
        try (InputStream i = in; OutputStream o = out) {
            byte[] buf = new byte[65536];
            int n;
            while ((n = i.read(buf)) > 0) o.write(buf, 0, n);
        }
    }

    private void copyAssetDir(String src, File dst) throws Exception {
        dst.mkdirs();
        String[] names = getAssets().list(src);
        if (names == null) return;
        for (String n : names) {
            String path = src + "/" + n;
            String[] sub = getAssets().list(path);
            if (sub != null && sub.length > 0) copyAssetDir(path, new File(dst, n));
            else copy(getAssets().open(path), new FileOutputStream(new File(dst, n)));
        }
    }

    private void js(String code) { runOnUiThread(() -> web.evaluateJavascript(code, null)); }

    private void toast(String msg) { js("window.nativeToast && window.nativeToast(" + JSONObject.quote(msg) + ")"); }

    // ------------------------------------------------------------------ USB

    private static boolean isBladeRF(UsbDevice d) {
        return (d.getVendorId() == 0x2cf0 && (d.getProductId() == 0x5250 || d.getProductId() == 0x5246))
            || (d.getVendorId() == 0x1d50 && d.getProductId() == 0x6066);
    }

    private UsbDevice findBladeRF() {
        for (UsbDevice d : usb.getDeviceList().values()) if (isBladeRF(d)) return d;
        return null;
    }

    @SuppressWarnings("deprecation")
    private static UsbDevice deviceFrom(Intent i) {
        if (Build.VERSION.SDK_INT >= 33) return i.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice.class);
        return i.getParcelableExtra(UsbManager.EXTRA_DEVICE);
    }

    private void askPermission(UsbDevice dev) {
        int flags = Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
        PendingIntent pi = PendingIntent.getBroadcast(this, 0,
            new Intent(ACTION_USB_PERMISSION).setPackage(getPackageName()), flags);
        usb.requestPermission(dev, pi);
    }

    /** Opens the device; returns the descriptor or -1. Keeps the connection. */
    private int openUsb(UsbDevice dev) {
        if (usbConn != null) usbConn.close();
        usbConn = null;
        UsbDeviceConnection c = usb.openDevice(dev);
        if (c == null) return -1;
        usbConn = c;
        usbDev = dev;
        return c.getFileDescriptor();
    }

    private void connectUsb(UsbDevice dev) {
        if (!usb.hasPermission(dev)) { askPermission(dev); return; }
        worker.execute(() -> {
            int fd = openUsb(dev);
            if (fd < 0) { toast("USB: could not open the bladeRF"); return; }
            call("set_usb_fd", fd);
            js("window.onUsbResult && window.onUsbResult()");
        });
    }

    private final BroadcastReceiver usbReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context ctx, Intent intent) {
            String a = intent.getAction();
            if (ACTION_USB_PERMISSION.equals(a)) {
                UsbDevice dev = deviceFrom(intent);
                if (dev == null) dev = findBladeRF();
                boolean ok = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false);
                if (dev != null && (ok || usb.hasPermission(dev))) connectUsb(dev);
                else toast("USB permission denied");
            } else if (UsbManager.ACTION_USB_DEVICE_ATTACHED.equals(a)) {
                UsbDevice dev = deviceFrom(intent);
                if (dev != null && isBladeRF(dev)) connectUsb(dev);
            } else if (UsbManager.ACTION_USB_DEVICE_DETACHED.equals(a)) {
                UsbDevice dev = deviceFrom(intent);
                if (dev == null || isBladeRF(dev)) {
                    worker.execute(() -> {
                        call("usb_detached");
                        if (usbConn != null) usbConn.close();
                        usbConn = null;
                        usbDev = null;
                    });
                }
            }
        }
    };

    // --------------------------------------------------------- file pickers

    private final ActivityResultLauncher<String[]> picker =
        registerForActivityResult(new ActivityResultContracts.OpenDocument(), this::onPicked);

    private void onPicked(Uri uri) {
        if ("upload".equals(pickKind)) {
            if (fileCallback != null) fileCallback.onReceiveValue(uri == null ? null : new Uri[]{uri});
            fileCallback = null;
            pickKind = "";
            return;
        }
        if (uri == null) return;
        final String kind = pickKind;
        worker.execute(() -> {
            try {
                if ("fpga".equals(kind)) {
                    File dir = new File(appData, "fpga");
                    dir.mkdirs();
                    File out = new File(dir, "hostedxA4.rbf");
                    copy(getContentResolver().openInputStream(uri), new FileOutputStream(out));
                    toast("FPGA image saved (" + (out.length() / 1024) + " kB)");
                    if (usbDev != null) connectUsb(usbDev);
                } else if ("presets".equals(kind)) {
                    StringBuilder sb = new StringBuilder();
                    try (BufferedReader r = new BufferedReader(new InputStreamReader(
                            getContentResolver().openInputStream(uri), StandardCharsets.UTF_8))) {
                        String line;
                        while ((line = r.readLine()) != null) sb.append(line).append('\n');
                    }
                    js("window.onPresetsPicked && window.onPresetsPicked(" + JSONObject.quote(sb.toString()) + ")");
                }
            } catch (Exception e) {
                toast("Could not read the file: " + e.getMessage());
            }
        });
    }

    // ------------------------------------------------------------- location
    //
    // The phone's own GPS (plus network location as a quick first fix). The
    // bladeRF 2.0 micro has no GPS receiver and none is needed: every
    // spectrum is tagged with where the PHONE was.

    // All four methods spelled out: before Android 10 the last three were
    // abstract, and leaving them out would crash there.
    private final LocationListener locListener = new LocationListener() {
        @Override public void onLocationChanged(Location l) { onFix(l); }
        @Override public void onStatusChanged(String p, int s, Bundle b) { }
        @Override public void onProviderEnabled(String p) { }
        @Override public void onProviderDisabled(String p) { }
    };

    private void onFix(Location l) {
        Location prev = lastFix;
        // a network fix must not overwrite a recent, better GPS fix
        if (prev != null && !LocationManager.GPS_PROVIDER.equals(l.getProvider())
            && LocationManager.GPS_PROVIDER.equals(prev.getProvider())
            && l.getTime() - prev.getTime() < 10_000 && l.getAccuracy() > prev.getAccuracy()) return;
        lastFix = l;
        Double acc = l.hasAccuracy() ? (double) l.getAccuracy() : null;
        Double alt = l.hasAltitude() ? l.getAltitude() : null;
        String prov = l.getProvider() != null ? l.getProvider() : "gps";
        worker.execute(() -> call("set_location", l.getLatitude(), l.getLongitude(), acc, alt, prov));
    }

    @SuppressLint("MissingPermission")
    private void startLocation() {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION}, REQ_LOCATION);
            return;
        }
        if (locOn) return;
        locOn = true;
        for (String p : new String[]{LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER}) {
            try {
                if (loc.isProviderEnabled(p)) {
                    loc.requestLocationUpdates(p, 1000L, 0f, locListener);
                    Location last = loc.getLastKnownLocation(p);
                    if (last != null) onFix(last);
                }
            } catch (Exception e) { Log.w(TAG, "location " + p, e); }
        }
        if (!loc.isProviderEnabled(LocationManager.GPS_PROVIDER)) toast("GPS is off in the phone settings");
    }

    private void stopLocation() {
        if (locOn) try { loc.removeUpdates(locListener); } catch (Exception ignored) { }
        locOn = false;
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms, int[] res) {
        super.onRequestPermissionsResult(code, perms, res);
        if (code == REQ_LOCATION) {
            boolean any = false;
            for (int r : res) any |= r == PackageManager.PERMISSION_GRANTED;
            if (any) startLocation();
            else toast("Location permission denied: points will be saved without a position");
        }
    }

    // ------------------------------------------------- "Save as" destination

    private final ActivityResultLauncher<Intent> saveAsLauncher =
        registerForActivityResult(new ActivityResultContracts.StartActivityForResult(), r -> {
            File src = saveAsSource;
            saveAsSource = null;
            Uri uri = r.getData() != null ? r.getData().getData() : null;
            if (r.getResultCode() != RESULT_OK || uri == null || src == null) return;
            worker.execute(() -> {
                try {
                    copy(new java.io.FileInputStream(src), getContentResolver().openOutputStream(uri));
                    toast("Saved: " + src.getName());
                } catch (Exception e) {
                    toast("Save failed: " + e.getMessage());
                }
            });
        });

    // ------------------------------------------------------- JS -> Android

    public class Bridge {
        @JavascriptInterface
        public void setOrientation(String mode) {
            runOnUiThread(() -> setRequestedOrientation(
                "portrait".equals(mode) ? ActivityInfo.SCREEN_ORIENTATION_SENSOR_PORTRAIT
                    : "landscape".equals(mode) ? ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
                    // follows the sensor even when the phone's own rotation lock is on
                    : ActivityInfo.SCREEN_ORIENTATION_FULL_SENSOR));
        }

        @JavascriptInterface
        public void keepScreenOn(boolean on) {
            runOnUiThread(() -> {
                if (on) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                else getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            });
        }

        @JavascriptInterface
        public void requestUsb() {
            runOnUiThread(() -> {
                UsbDevice dev = findBladeRF();
                if (dev == null) toast("No bladeRF on USB. Check the OTG cable and power.");
                else connectUsb(dev);
            });
        }

        @JavascriptInterface
        public void share(String path) {
            runOnUiThread(() -> {
                try {
                    File f = new File(path);
                    Uri uri = FileProvider.getUriForFile(MainActivity.this, getPackageName() + ".files", f);
                    String n = f.getName().toLowerCase();
                    String type = n.endsWith(".png") ? "image/png"
                        : n.endsWith(".csv") ? "text/csv"
                        : n.endsWith(".json") ? "application/json"
                        : n.endsWith(".xlsx") ? "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        : (n.endsWith(".jpg") || n.endsWith(".jpeg")) ? "image/jpeg"
                        : n.endsWith(".kml") ? "application/vnd.google-earth.kml+xml"
                        : n.endsWith(".webm") ? "video/webm"
                        : "application/octet-stream";
                    Intent send = new Intent(Intent.ACTION_SEND);
                    send.setType(type);
                    send.putExtra(Intent.EXTRA_STREAM, uri);
                    send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    startActivity(Intent.createChooser(send, f.getName()));
                } catch (Exception e) {
                    toast("Share failed: " + e.getMessage());
                }
            });
        }

        @JavascriptInterface
        public void startLocation() { runOnUiThread(MainActivity.this::startLocation); }

        /** {"perm":"granted|denied","gps":bool,"net":bool} -- polled by the
         *  page so a refused permission or GPS switched off is never silent. */
        @JavascriptInterface
        public String locationStatus() {
            boolean granted = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
            boolean gps = false, net = false;
            try { gps = loc.isProviderEnabled(LocationManager.GPS_PROVIDER); } catch (Exception ignored) { }
            try { net = loc.isProviderEnabled(LocationManager.NETWORK_PROVIDER); } catch (Exception ignored) { }
            return "{\"perm\":\"" + (granted ? "granted" : "denied") + "\",\"gps\":" + gps + ",\"net\":" + net + "}";
        }

        /** The red "location is off" bar was tapped: ask again, or open the
         *  settings page that can fix it (permission refused for good, or GPS off). */
        @JavascriptInterface
        public void fixLocation() {
            runOnUiThread(() -> {
                boolean granted = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                    || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
                try {
                    if (!granted) {
                        if (shouldShowRequestPermissionRationale(Manifest.permission.ACCESS_FINE_LOCATION) || !askedLocation) {
                            askedLocation = true;
                            MainActivity.this.startLocation();
                        } else {
                            Intent i = new Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                Uri.fromParts("package", getPackageName(), null));
                            startActivity(i);
                        }
                    } else if (!loc.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                        startActivity(new Intent(android.provider.Settings.ACTION_LOCATION_SOURCE_SETTINGS));
                    } else {
                        locOn = false;
                        MainActivity.this.startLocation();
                    }
                } catch (Exception e) { toast("Open Settings > Apps > YT Spectrum Analyzer > Permissions > Location"); }
            });
        }

        /** Copy a file the app wrote to a place the user picks (Downloads, Drive, ...). */
        @JavascriptInterface
        public void saveAs(String path, String name, String mime) {
            runOnUiThread(() -> {
                File f = new File(path);
                if (!f.isFile()) { toast("File not found: " + f.getName()); return; }
                saveAsSource = f;
                Intent i = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                i.addCategory(Intent.CATEGORY_OPENABLE);
                i.setType(mime == null || mime.trim().isEmpty() ? "application/octet-stream" : mime);
                i.putExtra(Intent.EXTRA_TITLE, name);
                saveAsLauncher.launch(i);
            });
        }

        @JavascriptInterface
        public void pickFpga() {
            runOnUiThread(() -> { pickKind = "fpga"; picker.launch(new String[]{"*/*"}); });
        }

        @JavascriptInterface
        public void pickPresets() {
            runOnUiThread(() -> { pickKind = "presets"; picker.launch(new String[]{"application/json", "text/*", "*/*"}); });
        }
    }
}
