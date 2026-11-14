plugins {
    kotlin("jvm") version "2.0.21"
    kotlin("plugin.serialization") version "2.0.21"
    application
}

dependencies {
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    testImplementation(kotlin("test-junit"))
}

application {
    mainClass.set("com.wludy.rolithax.console.sdk.MainKt")
    applicationName = "rolithax-console-sdk"
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin.compilerOptions {
    jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
}

tasks.processResources {
    from("LICENSE") { into("template") }
}

distributions {
    main {
        contents {
            from("LICENSE")
            from("README.md")
            from("spec") { into("spec") }
            from("skills/rolithax-console-plugin") { into("skills/rolithax-console-plugin") }
        }
    }
}
