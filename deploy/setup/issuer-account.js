// The issuer's database account, with rights on its own database alone: made once, as the database is first made
// (mongo's /docker-entrypoint-initdb.d). The API server's account is made by its admin tool, at every start.
db.getSiblingDB("decentai_issuer").createUser({
  user: "issuer",
  pwd: process.env.ISSUER_DATABASE_PASSWORD,
  roles: [{role: "readWrite", db: "decentai_issuer"}],
});
