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
        versionCode = 1
        versionName = "1.0"
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
    implementation(platform("org.jetbrains.kotlin:kotlin-bom:1.8.22"))
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.9.3")
}
