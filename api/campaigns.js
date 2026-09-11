import supabase from './db-client.js';

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');

  if (req.method === 'OPTIONS') return res.status(204).end();

  try {
    if (req.method === 'GET') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      const client = token
          ? new (await import('@supabase/supabase-js')).default(
            process.env.NEXT_PUBLIC_SUPABASE_URL,
            process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
            { global: { headers: { Authorization: `Bearer ${token}` } } }
          )
        : supabase;

      const { query } = req;
      let q = client.from('campaigns').select('*').order('created_at', { ascending: false });

      if (query.status) q = q.eq('status', query.status);
      if (query.id) q = q.eq('id', query.id);

      const { data, error } = await q;
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

      const { data: { user }, error: userError } = await client.auth.getUser(token);
      if (userError || !user) return res.status(401).json({ error: 'Invalid token' });

      const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      const { name, description, employee_id, total_contacts } = body;

      const { data, error } = await client.from('campaigns').insert({
        user_id: user.id,
        name, description, employee_id,
        total_contacts: total_contacts || 0,
        status: 'draft',
      }).select().single();

      if (error) throw error;
      return res.status(201).json(data);
    }

    if (req.method === 'PUT') {
      const token = req.headers.authorization?.replace('Bearer ', '');
      if (!token) return res.status(401).json({ error: 'Unauthorized' });

      const { createClient } = await import('@supabase/supabase-js');
      const client = new (await import('@supabase/supabase-js')).default(
          process.env.NEXT_PUBLIC_SUPABASE_URL,
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
        { global: { headers: { Authorization: `Bearer ${token}` } } }
      );

      const { data: { user }, error: userError } = await client.auth.getUser(token);
      if (userError || !user) return res.status(401).json({ error: 'Invalid token' });

      const body = typeof req.body === 'string' ? JSON.parse(req.body) : req.body;
      const { id, action } = body;

      let updates = {};

      if (action === 'start') {
        updates = { status: 'running', is_active: true, started_at: new Date().toISOString() };
      } else if (action === 'pause') {
        updates = { status: 'paused', paused_at: new Date().toISOString() };
      } else if (action === 'resume') {
        updates = { status: 'running', paused_at: null };
      } else if (action === 'stop') {
        updates = { status: 'completed', is_active: false, completed_at: new Date().toISOString() };
      } else if (action === 'update') {
        updates = body.updates;
      }

      const { data, error } = await client
        .from('campaigns')
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
        .from('campaigns')
        .delete()
        .eq('id', id)
        .eq('user_id', user.id);

      if (error) throw error;
      return res.status(200).json({ ok: true });
    }

    res.status(405).json({ error: 'Method not allowed' });
  } catch (err) {
    console.error('Campaigns API error:', err);
    res.status(500).json({ error: err.message });
  }
}
