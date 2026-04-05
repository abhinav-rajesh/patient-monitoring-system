import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:socket_io_client/socket_io_client.dart' as IO;
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:shared_preferences/shared_preferences.dart';

// Your laptop's local IP where Python Flask is running
// Default laptop IP (can be changed in the app UI now!)
String SERVER_URL = 'http://192.168.0.100:5000';

/// Setup Flutter Local Notifications Plugin
final FlutterLocalNotificationsPlugin localNotif = FlutterLocalNotificationsPlugin();

/// Background message handler for FCM (MUST be a top-level function)
@pragma('vm:entry-point')
Future<void> _firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  await Firebase.initializeApp();
  print("Background push received: ${message.messageId}");
}

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  // Initialize Firebase to get Android lock-screen push working!
  await Firebase.initializeApp();
  FirebaseMessaging.onBackgroundMessage(_firebaseMessagingBackgroundHandler);

  // Request Notification Permissions (REQUIRED for Android 13+)
  await FirebaseMessaging.instance.requestPermission(
    alert: true,
    badge: true,
    sound: true,
    criticalAlert: true,
  );

  // Initialize Local Notifications (for the System Tray Banner)
  const androidInit = AndroidInitializationSettings('@mipmap/ic_launcher');
  const iosInit = DarwinInitializationSettings();
  await localNotif.initialize(const InitializationSettings(android: androidInit, iOS: iosInit));

  runApp(const MedWatchApp());
}

class MedWatchApp extends StatelessWidget {
  const MedWatchApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      title: 'MedWatch Mobile',
      theme: ThemeData.dark().copyWith(
        scaffoldBackgroundColor: const Color(0xFF0F172A),
        appBarTheme: const AppBarTheme(color: Color(0xFF1E293B)),
      ),
      home: const LoginPage(),
    );
  }
}

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});
  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final TextEditingController _nurseIdCtrl = TextEditingController();
  final TextEditingController _serverUrlCtrl = TextEditingController(text: '192.168.0.100');
  bool _isLoading = false;

  Future<void> _login() async {
    if (_nurseIdCtrl.text.isEmpty || _serverUrlCtrl.text.isEmpty) return;
    
    setState(() {
      SERVER_URL = 'http://${_serverUrlCtrl.text.trim()}:5000';
      _isLoading = true;
    });
    
    // Register device for FCM Push tokens
    try {
      String? fcmToken = await FirebaseMessaging.instance.getToken();
      if (fcmToken != null) {
        final resp = await http.post(
          Uri.parse('$SERVER_URL/api/register_fcm_push'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({
            'nurse_id': _nurseIdCtrl.text.trim(),
            'token': fcmToken,
          }),
        ).timeout(const Duration(seconds: 10));
        print("Backend mapping response: ${resp.statusCode}");
      } else {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Firebase Token is NULL!')),
        );
      }
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('FCM Error: $e')),
      );
      print("FCM Setup Error: $e");
    }

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('nurse_id', _nurseIdCtrl.text.trim());

    if (!mounted) return;
    Navigator.pushReplacement(
      context, 
      MaterialPageRoute(builder: (_) => DashboardPage(nurseId: _nurseIdCtrl.text.trim()))
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24.0),
          child: Container(
            padding: const EdgeInsets.all(30),
            decoration: BoxDecoration(
              color: const Color(0xFF1E293B),
              borderRadius: BorderRadius.circular(16),
              border: Border.all(color: const Color(0xFF334155)),
            ),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text('MedWatch Nurse Login', style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold, color: Colors.lightBlueAccent)),
                const SizedBox(height: 10),
                const Text('True Lock-Screen Push Active 🔔', style: TextStyle(color: Colors.greenAccent)),
                const SizedBox(height: 30),
                TextField(
                  controller: _serverUrlCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Server IP (e.g., 192.168.1.10)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 15),
                TextField(
                  controller: _nurseIdCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Nurse ID (e.g., nurse1)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 25),
                SizedBox(
                  width: double.infinity,
                  height: 50,
                  child: ElevatedButton(
                    onPressed: _isLoading ? null : _login,
                    child: _isLoading ? const CircularProgressIndicator() : const Text('Login'),
                  ),
                )
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class DashboardPage extends StatefulWidget {
  final String nurseId;
  const DashboardPage({super.key, required this.nurseId});
  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  IO.Socket? socket;
  List<Map<String, dynamic>> alerts = [];

  @override
  void initState() {
    super.initState();
    _setupPushListeners();
    _connectSocket();
  }

  void _setupPushListeners() {
    // Show foreground push notifications in the Notification Bar natively
    FirebaseMessaging.onMessage.listen((RemoteMessage message) {
      if (message.notification != null) {
        localNotif.show(
          message.hashCode,
          message.notification!.title,
          message.notification!.body,
          const NotificationDetails(
            android: AndroidNotificationDetails(
              'critical_alerts', 'Critical Alerts',
              importance: Importance.max,
              priority: Priority.high,
            ),
          ),
        );
      }
    });
  }

  void _connectSocket() {
    socket = IO.io(SERVER_URL, <String, dynamic>{
      'transports': ['websocket'],
      'autoConnect': true,
    });

    socket!.on('alert_event', (data) {
      final List targetNurses = data['nurses'] ?? [];
      if (targetNurses.contains(widget.nurseId)) {
        setState(() {
          alerts.insert(0, {...data, 'time': DateTime.now().toString()});
        });
      }
    });
  }

  @override
  void dispose() {
    socket?.disconnect();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('Nurse ${widget.nurseId} - Dashboard'),
      ),
      body: alerts.isEmpty
          ? const Center(child: Text('All patients stable. Awaiting network alerts...', style: TextStyle(color: Colors.grey)))
          : ListView.builder(
              itemCount: alerts.length,
              itemBuilder: (context, index) {
                final al = alerts[index];
                final isCrit = al['level'] == 'critical';
                
                return Card(
                  color: isCrit ? Colors.red.withOpacity(0.2) : Colors.orange.withOpacity(0.2),
                  margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                  child: ListTile(
                    leading: Text(isCrit ? '🚨' : '⚠️', style: const TextStyle(fontSize: 24)),
                    title: Text('${al['patient_name']} (${al['vital']} - ${al['value']})', style: const TextStyle(fontWeight: FontWeight.bold)),
                    subtitle: Text(al['direction'] ?? 'Unknown Status'),
                    trailing: const Icon(Icons.arrow_forward_ios, size: 14),
                  ),
                );
              },
            ),
    );
  }
}
