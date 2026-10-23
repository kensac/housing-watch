import type { CodegenConfig } from '@graphql-codegen/cli'

// Types for every GraphQL document in src/, from the SDL the Python schema exports (`python -m housing_watch
// schema`). `npm run codegen` rewrites src/gql/; CI fails when it is stale or a document does not validate.
const config: CodegenConfig = {
  schema: '../schema.graphql',
  documents: ['src/**/*.{ts,tsx}', '!src/gql/**'],
  ignoreNoDocuments: false,
  generates: {
    'src/gql/': {
      preset: 'client',
      presetConfig: { fragmentMasking: false },
      config: {
        enumsAsTypes: true,
        strictScalars: true,
        scalars: { DateTime: 'string', JSON: 'Record<string, unknown>' },
        useTypeImports: true,
        avoidOptionals: { field: true, inputValue: false, object: true, defaultValue: false },
        skipTypename: true,
        dedupeFragments: true,
      },
    },
  },
}

export default config
