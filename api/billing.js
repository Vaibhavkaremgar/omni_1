import supabase from './db-client.js';

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
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
      let q = client.from('billing_records').select('*').order('created_at', { ascending: false });

      if (query.limit) q = q.limit(parseInt(query.limit, 10));

      const { data, error } = await q;
      if (error) throw error;

      // Compute summary
      const credits = data
        .filter(r => r.type === 'credit')
        .reduce((sum, r) => sum + parseFloat(r.amount), 0);
      const debits = data
        .filter(r => r.type === 'debit')
        .reduce((sum, r) => sum + parseFloat(r.amount), 0);
      const remaining = credits + debits; // debits are negative

      const totalCost = data
        .filter(r => r.type === 'debit')
        .reduce((sum, r) => sum + parseFloat(r.cost || '0'), 0);

      const totalMinutes = -debits; // debits represent minutes consumed (negative)
      const totalCalls = Math.round(totalMinutes / 2.5); // rough estimate based on avg call time

      return res.status(200).json({
        records: data,
        summary: {
          credits_added: credits,
          credits_used: Math.abs(debits),
          credits_remaining: Math.max(0, remaining),
          total_cost: Math.abs(totalCost),
          total_minutes_used: totalMinutes,
          total_calls: totalCalls,
        },
      });
    }

    res.status(405).json({ error: 'Method not allowed' });
  } catch (err) {
    console.error('Billing API error:', err);
    res.status(500).json({ error: err.message });
  }
}
