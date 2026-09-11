import supabase from './db-client.js';

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') return res.status(204).end();

  try {
    // Only server-side routes use the service-role client with RLS bypassed
    // For per-user scoping we create a scoped client in each route when needed

    if (req.method === 'GET') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      const client = token
        ? new (await import('@supabase/supabase-js')).default(
            process.env.NEXT_PUBLIC_SUPABASE_URL,
            process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
            { global: { headers: { Authorization: `Bearer ${token}` } } }
          )
        : supabase;

      const { data, error } = await client
        .from('employees')
        .select('*')
        .order('last_updated', { ascending: false });

      if (error) throw error;
      return res.status(200).json(data);
    }

    if (req.method === 'POST') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      if (!token) return res.status(401).json({ error: 'Unauthorized' });

      const { createClient } = await import('@supabase/supabase-js');
      const client = createClient(
        process.env.NEXT_PUBLIC_SUPABASE_URL,
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
        { global: { headers: { Authorization: `Bearer ${token}` } } }
      );

      // Get user from token to set user_id
      const { data: { user }, error: userError } = await client.auth.getUser(token);
      if (userError || !user) return res.status(401).json({ error: 'Invalid token' });

      const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      const { name, description, voice, language, prompt, tone, inbound_enabled, outbound_enabled, phone_number_id, status, is_active } = body;

      const { data, error } = await client.from('employees').insert({
        user_id: user.id,
        name, description, voice, language, prompt, tone,
        inbound_enabled: inbound_enabled ?? true,
        outbound_enabled: outbound_enabled ?? true,
        phone_number_id, status: status || 'draft', is_active: is_active ?? true,
      }).select().single();

      if (error) throw error;
      return res.status(201).json(data);
    }

    if (req.method === 'PUT') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      if (!token) return res.status(401).json({ error: 'Unauthorized' });

      const { createClient } = await import('@supabase/supabase-js');
      const client = createClient(
        process.env.NEXT_PUBLIC_SUPABASE_URL,
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
        { global: { headers: { Authorization: `Bearer ${token}` } } }
      );

      const { data: { user }, error: userError } = await client.auth.getUser(token);
      if (userError || !user) return res.status(401).json({ error: 'Invalid token' });

      const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      const { id, ...updates } = body;

      const { data, error } = await client
        .from('employees')
        .update(updates)
        .eq('id', id)
        .eq('user_id', user.id)
        .select()
        .single();

      if (error) throw error;
      return res.status(200).json(data);
    }

    if (req.method === 'DELETE') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      if (!token) return res.status(401).json({ error: 'Unauthorized' });

      const { createClient } = await import('@supabase/supabase-js');
      const client = createClient(
        process.env.NEXT_PUBLIC_SUPABASE_URL,
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
        { global: { headers: { Authorization: `Bearer ${token}` } } }
      );

      const { data: { user }, error: userError } = await client.auth.getUser(token);
      if (userError || !user) return res.status(401).json({ error: 'Invalid token' });

      const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      const { id } = body;

      const { error } = await client
        .from('employees')
        .delete()
        .eq('id', id)
        .eq('user_id', user.id);

      if (error) throw error;
      return res.status(200).json({ ok: true });
    }

    res.status(405).json({ error: 'Method not allowed' });
  } catch (err) {
    console.error('Employees API error:', err);
    res.status(500).json({ error: err.message });
  }
}
