package ru.solovyshka.food_checking_mobile

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.media.ExifInterface
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.os.Build
import android.provider.Settings
import android.content.pm.PackageInfo
import androidx.core.content.FileProvider
import java.security.MessageDigest
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.AtomicFile
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import com.google.zxing.integration.android.IntentIntegrator
import com.google.zxing.oned.UPCEReader
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
    private var imageResult: MethodChannel.Result? = null
    private var barcodeResult: MethodChannel.Result? = null
    private var verifiedUpdate: String? = null
    private val keyAlias = "food-diary-local-v1"
    private val libraryLock = Any()

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
                        "loadLibrary", "storeLibrary" -> {
                            val owner = call.argument<String>("owner")!!
                            val value = call.argument<String>("value")
                            val hash = MessageDigest.getInstance("SHA-256")
                                .digest(owner.toByteArray(Charsets.UTF_8))
                                .joinToString("") { "%02x".format(it) }
                            val saving = call.method == "storeLibrary"
                            Thread {
                                try {
                                  synchronized(libraryLock) {
                                    val directory = File(filesDir, "food-library")
                                    directory.mkdirs()
                                    val file = AtomicFile(File(directory, "$hash.bin"))
                                    if (saving) {
                                        val stream = file.startWrite()
                                        try {
                                            stream.write(encrypt(value!!).toByteArray(Charsets.UTF_8))
                                            file.finishWrite(stream)
                                        } catch (e: Exception) {
                                            file.failWrite(stream)
                                            throw e
                                        }
                                        runOnUiThread { result.success(null) }
                                    } else {
                                        val data = if (file.baseFile.exists()) decrypt(String(file.readFully(), Charsets.UTF_8)) else null
                                        runOnUiThread { result.success(data) }
                                    }
                                  }
                                } catch (e: Exception) {
                                    runOnUiThread { result.error("library", "Не удалось сохранить или прочитать справочник", null) }
                                }
                            }.start()
                        }
                        "scanBarcode" -> {
                            if (barcodeResult != null || imageResult != null || recorder != null || permissionResult != null) {
                                result.error("busy", "Дождитесь завершения текущего действия", null)
                            } else {
                                barcodeResult = result
                                try {
                                    IntentIntegrator(this)
                                        .setCaptureActivity(FoodBarcodeActivity::class.java)
                                        .setRequestCode(43)
                                        .setDesiredBarcodeFormats(listOf("EAN_13", "EAN_8", "UPC_A", "UPC_E"))
                                        .setPrompt("Наведите камеру на штрихкод упаковки")
                                        .setBeepEnabled(false)
                                        .setOrientationLocked(true)
                                        .initiateScan()
                                } catch (e: Exception) {
                                    barcodeResult = null
                                    result.error("camera", "Не удалось открыть сканер. Можно ввести код вручную", null)
                                }
                            }
                        }
                        "pickImage" -> {
                            if (imageResult != null || barcodeResult != null || recorder != null || permissionResult != null) {
                                result.error("busy", "Дождитесь завершения текущего действия", null)
                            } else {
                                val intent = Intent(Intent.ACTION_OPEN_DOCUMENT)
                                    .addCategory(Intent.CATEGORY_OPENABLE)
                                    .setType("image/*")
                                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                imageResult = result
                                try {
                                    startActivityForResult(intent, 42)
                                } catch (e: Exception) {
                                    imageResult = null
                                    result.error("image", "Не удалось открыть выбор картинки", null)
                                }
                            }
                        }
                        "deleteImage" -> {
                            val file = File(call.argument<String>("path")!!).canonicalFile
                            val directory = File(filesDir, "draft-images").canonicalFile
                            if (file.parentFile != directory || !file.name.endsWith(".jpg")) {
                                throw IllegalArgumentException("Неверный файл картинки")
                            }
                            if (file.exists() && !file.delete()) {
                                throw IllegalStateException("Не удалось удалить картинку")
                            }
                            result.success(null)
                        }
                        "startRecording" -> {
                            if (permissionResult != null || recorder != null || barcodeResult != null || imageResult != null) {
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
    @Synchronized
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
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 43) {
            val result = barcodeResult ?: return
            barcodeResult = null
            if (data?.getBooleanExtra("MISSING_CAMERA_PERMISSION", false) == true) {
                result.error("permission", "Разрешите доступ к камере или введите штрихкод вручную", null)
            } else {
                val contents = data?.getStringExtra("SCAN_RESULT")
                val format = data?.getStringExtra("SCAN_RESULT_FORMAT")
                val code = if (contents != null && format == "UPC_E") UPCEReader.convertUPCEtoUPCA(contents) else contents
                result.success(if (resultCode == RESULT_OK) code else null)
            }
            return
        }
        if (requestCode != 42) return
        val result = imageResult ?: return
        val uri = data?.data
        if (resultCode != RESULT_OK || uri == null) {
            imageResult = null
            result.success(null)
            return
        }
        Thread {
            try {
                val file = importImage(uri)
                runOnUiThread { imageResult = null; result.success(file.absolutePath) }
            } catch (e: Exception) {
                runOnUiThread {
                    imageResult = null
                    result.error("image", e.message ?: "Не удалось открыть картинку", null)
                }
            }
        }.start()
    }
    private fun importImage(uri: Uri): File {
        val source = File.createTempFile("food-image-source-", ".tmp", cacheDir)
        var output: File? = null
        var bitmap: Bitmap? = null
        try {
            contentResolver.openInputStream(uri)?.use { input ->
                source.outputStream().use { destination ->
                    val buffer = ByteArray(64 * 1024)
                    var total = 0L
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        total += count
                        if (total > 20L * 1024 * 1024) {
                            throw IllegalArgumentException("Выберите картинку размером до 20 МБ")
                        }
                        destination.write(buffer, 0, count)
                    }
                }
            } ?: throw IllegalArgumentException("Не удалось прочитать картинку")
            val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeFile(source.absolutePath, bounds)
            if (bounds.outWidth <= 0 || bounds.outHeight <= 0 ||
                bounds.outWidth > 32768 || bounds.outHeight > 32768 ||
                bounds.outWidth.toLong() * bounds.outHeight > 200_000_000) {
                throw IllegalArgumentException("Не удалось открыть картинку. Выберите другое изображение")
            }
            var sample = 1
            while (maxOf(bounds.outWidth, bounds.outHeight) / sample > 1600) sample *= 2
            val decoded = BitmapFactory.decodeFile(source.absolutePath,
                BitmapFactory.Options().apply { inSampleSize = sample })
                ?: throw IllegalArgumentException("Формат картинки не поддерживается")
            bitmap = decoded
            val orientation = try {
                ExifInterface(source.absolutePath).getAttributeInt(ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL)
            } catch (_: Exception) { ExifInterface.ORIENTATION_NORMAL }
            val matrix = Matrix()
            when (orientation) {
                ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.setScale(-1f, 1f)
                ExifInterface.ORIENTATION_ROTATE_180 -> matrix.setRotate(180f)
                ExifInterface.ORIENTATION_FLIP_VERTICAL -> matrix.setScale(1f, -1f)
                ExifInterface.ORIENTATION_TRANSPOSE -> { matrix.setRotate(90f); matrix.postScale(-1f, 1f) }
                ExifInterface.ORIENTATION_ROTATE_90 -> matrix.setRotate(90f)
                ExifInterface.ORIENTATION_TRANSVERSE -> { matrix.setRotate(270f); matrix.postScale(-1f, 1f) }
                ExifInterface.ORIENTATION_ROTATE_270 -> matrix.setRotate(270f)
            }
            if (!matrix.isIdentity) {
                bitmap = Bitmap.createBitmap(decoded, 0, 0, decoded.width, decoded.height, matrix, true)
                if (bitmap !== decoded) decoded.recycle()
            }
            val directory = File(filesDir, "draft-images").apply { mkdirs() }
            val destination = File.createTempFile("food-image-", ".jpg", directory)
            output = destination
            // A private JPEG copy keeps the draft independent of gallery access and strips EXIF metadata.
            destination.outputStream().use {
                if (!bitmap!!.compress(Bitmap.CompressFormat.JPEG, 85, it)) {
                    throw IllegalStateException("Не удалось сохранить картинку")
                }
            }
            return destination
        } catch (e: Exception) {
            output?.delete()
            throw e
        } finally {
            bitmap?.recycle()
            source.delete()
        }
    }
    override fun onPause() { cancelRecording(); super.onPause() }
    override fun onDestroy() { cancelRecording(); super.onDestroy() }
}
