plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

android {
    namespace = "com.netline.bladerfmobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.netline.bladerfmobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 12
        versionName = "1.2"
        ndk {
            // arm64 = every current phone; x86_64 = the Android Studio emulator
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
        externalNativeBuild {
            cmake {
                arguments += listOf("-DANDROID_STL=none")
            }
        }
    }

    // libbladeRF + libusb, built from the vendored sources (see native/).
    externalNativeBuild {
        cmake {
            path = file("../../native/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    // ONE fixed signing key, kept in the repository. Without it every GitHub
    // build signs with a fresh random debug key, and Android refuses to
    // install a new build over the old one ("app not installed" / the old
    // version silently stays). Fine for in-house use; not a store key.
    signingConfigs {
        getByName("debug") {
            storeFile = file("ci-debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Signed with the debug key so "Build APK" gives an installable
            // file without setting up a keystore. Replace for a store release.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging {
        jniLibs {
            // ctypes opens libbladeRF.so by path, so it must be a real file
            useLegacyPackaging = true
        }
    }
}

chaquopy {
    defaultConfig {
        // Must match a Python installed on the BUILD PC (Chaquopy runs pip
        // with it to fetch numpy for Android). If Gradle says it cannot find
        // Python 3.11, either install 3.11 from python.org or point at it:
        //     buildPython("C:/Users/<you>/AppData/Local/Programs/Python/Python311/python.exe")
        version = "3.11"
        // Set by the GitHub Actions build; on a PC Chaquopy finds Python 3.11 itself.
        System.getenv("CHAQUOPY_PYTHON")?.let { buildPython(it) }
        pip {
            install("numpy")
        }
    }
}

dependencies {
    // androidx pulls kotlin-stdlib 1.8.x while an older library still pulls
    // kotlin-stdlib-jdk7/jdk8 1.6.21: the same classes twice. The BOM lines
    // them all up on one version (the jdk7/jdk8 jars are empty from 1.8 on).
    implementation(platform("org.jetbrains.kotlin:kotlin-bom:1.8.22"))
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.3")
}
