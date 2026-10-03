package ru.solovyshka.food_checking_mobile

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.os.Build
import android.provider.Settings
import android.content.pm.PackageInfo
import androidx.core.content.FileProvider
import java.security.MessageDigest
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class MainActivity : FlutterActivity() {
    private var recorder: MediaRecorder? = null
    private var recordingFile: File? = null
    private var permissionResult: MethodChannel.Result? = null
    private var verifiedUpdate: String? = null
    private val keyAlias = "food-diary-local-v1"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "ru.solovyshka.food_checking/device")
            .setMethodCallHandler { call, result ->
                try {
                    when (call.method) {
                        "updatesDirectory" -> {
                            val directory = File(cacheDir, "updates")
                            directory.mkdirs()
                            result.success(directory.absolutePath)
                        }
                        "verifyUpdate" -> {
                            val file = updateFile(call.argument<String>("path")!!)
                            val expectedHash = call.argument<String>("sha256")!!
                            val expectedCode = call.argument<Number>("versionCode")!!.toLong()
                            Thread {
                                try {
                                    verifyUpdate(file, expectedHash, expectedCode)
                                    runOnUiThread { verifiedUpdate = file.canonicalPath; result.success(null) }
                                } catch (e: Exception) {
                                    runOnUiThread { result.error("update", e.message ?: "Не удалось проверить обновление", null) }
                                }
                            }.start()
                        }
                        "installUpdate" -> {
                            val file = updateFile(call.argument<String>("path")!!)
                            if (file.canonicalPath != verifiedUpdate) throw IllegalStateException("Сначала проверьте обновление")
                            if (!packageManager.canRequestPackageInstalls()) {
                                startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
                                result.success("permission_required")
                            } else {
                                val uri = FileProvider.getUriForFile(this, "$packageName.updates", file)
                                val intent = Intent(Intent.ACTION_VIEW)
                                    .setDataAndType(uri, "application/vnd.android.package-archive")
                                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                startActivity(intent)
                                result.success("installer_opened")
                            }
                        }
                        "appInfo" -> {
                            val info = packageManager.getPackageInfo(packageName, 0)
                            val code = if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()
                            result.success(mapOf("versionCode" to code, "versionName" to info.versionName))
                        }
                        "openUrl" -> {
                            val uri = Uri.parse(call.argument<String>("url")!!)
                            if (uri.scheme != "https") throw IllegalArgumentException("Требуется HTTPS-адрес")
                            startActivity(Intent(Intent.ACTION_VIEW, uri))
                            result.success(null)
                        }
                        "load" -> {
                            val data = getSharedPreferences("diary", MODE_PRIVATE).getString(call.argument<String>("key"), null)
                            result.success(if (data == null) null else decrypt(data))
                        }
                        "store" -> {
                            val key = call.argument<String>("key")!!
                            val value = call.argument<String>("value")!!
                            if (!getSharedPreferences("diary", MODE_PRIVATE).edit().putString(key, encrypt(value)).commit()) {
                                throw IllegalStateException("Не удалось сохранить данные телефона")
                            }
                            result.success(null)
                        }
                        "startRecording" -> {
                            if (permissionResult != null || recorder != null) {
                                result.error("busy", "Запись уже запущена", null)
                            } else if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                                permissionResult = result
                                requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 41)
                            } else startRecording(result)
                        }
                        "stopRecording" -> {
                            val current = recorder ?: throw IllegalStateException("Запись уже остановлена")
                            try {
                                current.stop()
                                result.success(recordingFile!!.absolutePath)
                            } catch (e: Exception) {
                                recordingFile?.delete()
                                result.error("short", "Запись слишком короткая. Попробуйте ещё раз", null)
                            } finally {
                                current.release()
                                recorder = null
                                recordingFile = null
                            }
                        }
                        "cancelRecording" -> { cancelRecording(); result.success(null) }
                        else -> result.notImplemented()
                    }
                } catch (e: Exception) {
                    result.error("device", e.message ?: "Ошибка устройства", null)
                }
            }
    }
    private fun updateFile(path: String): File {
        val file = File(path).canonicalFile
        val allowed = File(cacheDir, "updates").canonicalFile
        if (file.parentFile != allowed || !file.name.endsWith(".apk") || !file.isFile) {
            throw IllegalArgumentException("Неверный файл обновления")
        }
        return file
    }
    private fun packageCode(info: PackageInfo): Long =
        if (Build.VERSION.SDK_INT >= 28) info.longVersionCode else info.versionCode.toLong()
    private fun signers(info: PackageInfo): Set<String> {
        val signatures = if (Build.VERSION.SDK_INT >= 28) info.signingInfo?.apkContentsSigners else info.signatures
        if (signatures.isNullOrEmpty()) throw IllegalArgumentException("У приложения нет подписи")
        return signatures.map { signature ->
            MessageDigest.getInstance("SHA-256").digest(signature.toByteArray()).joinToString("") { "%02x".format(it) }
        }.toSet()
    }
    private fun verifyUpdate(file: File, hash: String, expectedCode: Long) {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        val actual = digest.digest().joinToString("") { "%02x".format(it) }
        if (actual != hash) throw IllegalArgumentException("Файл загрузился с ошибкой. Повторите скачивание")
        val flags = if (Build.VERSION.SDK_INT >= 28) PackageManager.GET_SIGNING_CERTIFICATES else PackageManager.GET_SIGNATURES
        val archive = packageManager.getPackageArchiveInfo(file.absolutePath, flags)
            ?: throw IllegalArgumentException("Некорректный APK")
        val installed = packageManager.getPackageInfo(packageName, flags)
        if (archive.packageName != packageName || packageCode(archive) != expectedCode ||
            packageCode(archive) <= packageCode(installed) || signers(archive) != signers(installed)) {
            throw IllegalArgumentException("Обновление не соответствует установленному приложению")
        }
    }
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(keyAlias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(keyAlias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    private fun encrypt(text: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encoder = { bytes: ByteArray -> Base64.encodeToString(bytes, Base64.NO_WRAP) }
        return encoder(cipher.iv) + ":" + encoder(cipher.doFinal(text.toByteArray(Charsets.UTF_8)))
    }
    private fun decrypt(text: String): String {
        val parts = text.split(":", limit=2)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)))
        return String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), Charsets.UTF_8)
    }
    private fun startRecording(result: MethodChannel.Result) {
        val file = File.createTempFile("food-voice-", ".m4a", cacheDir)
        val current = if (Build.VERSION.SDK_INT >= 31) MediaRecorder(this) else MediaRecorder()
        try {
            current.setAudioSource(MediaRecorder.AudioSource.MIC)
            current.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            current.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            current.setAudioChannels(1)
            current.setAudioSamplingRate(16000)
            current.setAudioEncodingBitRate(64000)
            current.setOutputFile(file.absolutePath)
            current.prepare()
            current.start()
            recordingFile = file
            recorder = current
            result.success(null)
        } catch (e: Exception) {
            current.release()
            file.delete()
            result.error("microphone", "Не удалось включить микрофон", null)
        }
    }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 41) {
            val result = permissionResult
            permissionResult = null
            if (result != null) {
                if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) startRecording(result)
                else result.error("permission", "Разрешите доступ к микрофону в настройках телефона или введите текст", null)
            }
        }
    }
    private fun cancelRecording() {
        try { recorder?.stop() } catch (_: Exception) {}
        recorder?.release()
        recorder = null
        recordingFile?.delete()
        recordingFile = null
    }
    override fun onPause() { cancelRecording(); super.onPause() }
    override fun onDestroy() { cancelRecording(); super.onDestroy() }
}
