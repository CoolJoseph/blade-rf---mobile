// No Kotlin plugin: MainActivity is Java, so the build needs only the Android
// Gradle plugin (google()) and Chaquopy (Maven Central). Chaquopy 16.0
// supports AGP up to 8.7.
plugins {
    id("com.android.application") version "8.6.1" apply false
    id("com.chaquo.python") version "16.0.0" apply false
}
