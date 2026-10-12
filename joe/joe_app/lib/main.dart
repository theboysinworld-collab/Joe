import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

const String defaultBackendUrl = 'http://127.0.0.1:8765';

String resolveBackendUrl() {
  const fromEnv = String.fromEnvironment('BACKEND_URL', defaultValue: defaultBackendUrl);
  return fromEnv.trim().isEmpty ? defaultBackendUrl : fromEnv;
}

void main() {
  runApp(const MainApp());
}

class MainApp extends StatelessWidget {
  const MainApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Joe Native',
      theme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: const Color(0xFF6A5AE0),
      ),
      home: const JoeNativeScreen(),
    );
  }
}

class JoeNativeScreen extends StatefulWidget {
  const JoeNativeScreen({super.key});

  @override
  State<JoeNativeScreen> createState() => _JoeNativeScreenState();
}

class _JoeNativeScreenState extends State<JoeNativeScreen> {
  final TextEditingController _controller = TextEditingController();
  final List<ChatMessage> _messages = [
    const ChatMessage(
      role: 'assistant',
      text: 'Olá! Eu sou o Joe. Posso responder localmente e usar o backend do projeto quando o serviço estiver ativo.',
    ),
  ];

  bool _loading = false;
  bool _online = false;
  String _provider = 'offline';
  String _model = 'planner';
  int _tools = 0;
  final String _backendUrl = resolveBackendUrl();

  @override
  void initState() {
    super.initState();
    _refreshStatus();
  }

  Future<void> _refreshStatus() async {
    try {
      final response = await http.get(Uri.parse('$_backendUrl/api/status')).timeout(
        const Duration(seconds: 3),
      );
      if (!mounted) {
        return;
      }
      if (response.statusCode == 200) {
        final decoded = jsonDecode(response.body) as Map<String, dynamic>;
        setState(() {
          _online = true;
          _provider = decoded['provider']?.toString() ?? 'offline-planner';
          _model = decoded['model']?.toString() ?? 'planner';
          _tools = int.tryParse(decoded['tools']?.toString() ?? '0') ?? 0;
        });
        return;
      }
    } catch (_) {
      // backend ainda não está em execução; o cliente continua vivo e informa estado local.
    }

    if (!mounted) {
      return;
    }
    setState(() {
      _online = false;
      _provider = 'offline';
      _model = 'planner';
      _tools = 0;
    });
  }

  Future<void> _sendMessage() async {
    final raw = _controller.text.trim();
    if (raw.isEmpty || _loading) {
      return;
    }

    final userMessage = ChatMessage(role: 'user', text: raw);
    _controller.clear();
    setState(() {
      _messages.add(userMessage);
      _loading = true;
    });

    try {
      final response = await http.post(
        Uri.parse('$_backendUrl/api/chat'),
        headers: {'Content-Type': 'application/json; charset=utf-8'},
        body: jsonEncode({'message': raw}),
      ).timeout(const Duration(seconds: 12));

      if (!mounted) {
        return;
      }

      if (response.statusCode == 200) {
        final decoded = jsonDecode(response.body) as Map<String, dynamic>;
        final answer = (decoded['reply'] ?? 'Não foi possível recuperar a resposta do Joe.').toString();
        setState(() {
          _messages.add(ChatMessage(role: 'assistant', text: answer));
        });
      } else {
        final decoded = jsonDecode(response.body);
        final error = (decoded is Map<String, dynamic> ? decoded['error'] : null) ?? 'Falha ao comunicar com o backend.';
        setState(() {
          _messages.add(ChatMessage(role: 'assistant', text: 'Erro: $error'));
        });
      }
    } catch (_) {
      setState(() {
        _messages.add(const ChatMessage(
          role: 'assistant',
          text: 'O backend local não respondeu. Inicie o Joe em modo web ou via Python para ativar a IA.',
        ));
      });
    } finally {
      if (mounted) {
        setState(() {
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Joe Native'),
        actions: [
          Padding(
            padding: const EdgeInsets.only(right: 12),
            child: Chip(
              label: Text(_online ? 'Online' : 'Offline'),
              backgroundColor: _online ? Colors.green.shade100 : Colors.orange.shade100,
              labelStyle: TextStyle(
                color: _online ? Colors.green.shade900 : Colors.orange.shade900,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
        ],
      ),
      body: SafeArea(
        child: Column(
          children: [
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.primaryContainer.withAlpha(27),
              ),
              child: Wrap(
                spacing: 12,
                runSpacing: 8,
                children: [
                  _StatusPill(label: 'Backend', value: _backendUrl),
                  _StatusPill(label: 'Provider', value: _provider),
                  _StatusPill(label: 'Model', value: _model),
                  _StatusPill(label: 'Tools', value: '$_tools'),
                ],
              ),
            ),
            Expanded(
              child: ListView.builder(
                padding: const EdgeInsets.all(16),
                itemCount: _messages.length,
                itemBuilder: (context, index) {
                  final item = _messages[index];
                  final isUser = item.role == 'user';
                  return Align(
                    alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
                    child: Container(
                      margin: const EdgeInsets.only(bottom: 12),
                      constraints: const BoxConstraints(maxWidth: 640),
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                      decoration: BoxDecoration(
                        color: isUser
                            ? Theme.of(context).colorScheme.primary
                            : Theme.of(context).colorScheme.surfaceContainerHighest,
                        borderRadius: BorderRadius.circular(14),
                      ),
                      child: Text(
                        item.text,
                        style: TextStyle(
                          color: isUser ? Colors.white : Theme.of(context).colorScheme.onSurface,
                        ),
                      ),
                    ),
                  );
                },
              ),
            ),
            if (_loading)
              const Padding(
                padding: EdgeInsets.only(bottom: 8),
                child: CircularProgressIndicator(),
              ),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
              child: Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: _controller,
                      minLines: 1,
                      maxLines: 4,
                      decoration: InputDecoration(
                        hintText: 'Digite sua mensagem para o Joe…',
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                        ),
                        filled: true,
                      ),
                      onSubmitted: (_) => _sendMessage(),
                    ),
                  ),
                  const SizedBox(width: 12),
                  FilledButton.icon(
                    onPressed: _loading ? null : _sendMessage,
                    icon: const Icon(Icons.send_rounded),
                    label: const Text('Enviar'),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(999),
        color: Theme.of(context).colorScheme.surface,
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text('$label: ', style: const TextStyle(fontWeight: FontWeight.w600)),
          Text(value),
        ],
      ),
    );
  }
}

class ChatMessage {
  const ChatMessage({required this.role, required this.text});

  final String role;
  final String text;
}
